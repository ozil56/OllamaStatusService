"""Ollama status gateway. One process, durable admission, fail-closed recovery."""
from __future__ import annotations

import asyncio
from collections import deque
import ipaddress
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from model_jobs import ModelJobs, JobError
from manage import ManageService, install_routes

LOG = logging.getLogger("ollama_status")
VERSION = "1.4.0"
INFERENCE = {"/api/chat", "/api/generate", "/api/embed", "/api/embeddings",
             "/v1/chat/completions", "/v1/completions", "/v1/embeddings",
             "/v1/responses", "/v1/messages"}
EMBEDDING = {"/api/embed", "/api/embeddings", "/v1/embeddings"}
MODEL_TRANSFERS = {"/api/pull", "/api/push"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def client_origin(request: Request, proxy_port: int = 11436):
    """Use socket endpoints only. run.py must keep proxy_headers=False.

    The proxy listener is always remote, even for loopback peers. Its port comes
    from the accepted socket, never the HTTP Host or forwarding headers.
    On the direct listener, same-address and loopback connections remain local.
    """
    server = request.scope.get("server")
    if server and server[1] == proxy_port:
        return "remote"
    def address(value):
        try:
            value = ipaddress.ip_address(value.split("%", 1)[0])
            return getattr(value, "ipv4_mapped", None) or value
        except (ValueError, AttributeError):
            return None
    peer = address(request.client.host) if request.client else None
    local = address(server[0]) if server else None
    return "local" if peer and (peer.is_loopback or (
        peer == local and not peer.is_unspecified and not peer.is_multicast)) else "remote"


class DiscoveryHeaders:
    """Pure ASGI middleware; does not consume the body or interfere with disconnects."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def identified(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers = [(k, v) for k, v in headers if k.lower() != b'x-ollama-status-service']
                headers.extend([(b'x-ollama-status-service', VERSION.encode()),
                                (b'link', b'</help>; rel="service-desc"; type="application/json"')])
                message = dict(message, headers=headers)
            await send(message)
        await self.app(scope, receive, identified)


@dataclass
class Settings:
    backend_url: str = "http://127.0.0.1:11435"
    listen_host: str = "0.0.0.0"
    listen_ipv6: bool = True
    listen_port: int = 11434
    proxy_listen_host: str = "127.0.0.1"
    proxy_listen_port: int = 11436
    max_queue_size: int = 64
    max_queue_bytes: int = 64 * 1024 * 1024
    queue_timeout_seconds: float = 15
    max_queue_timeout_seconds: float = 3600
    data_dir: str = r"C:\ProgramData\OllamaStatusService\data"
    max_concurrency: int = 1
    request_deadline_seconds: float = 3600
    upstream_read_timeout_seconds: float = 600
    health_interval_seconds: float = 2
    health_timeout_seconds: float = 3
    max_body_bytes: int = 32 * 1024 * 1024
    max_response_bytes: int = 64 * 1024 * 1024
    client_write_timeout_seconds: float = 15
    shutdown_grace_seconds: float = 50

    def validate(self):
        from urllib.parse import urlparse
        if not isinstance(self.listen_host, str) or not self.listen_host.strip():
            raise ValueError("listen_host must be a non-empty address or hostname")
        if not isinstance(self.proxy_listen_host, str) or not self.proxy_listen_host.strip():
            raise ValueError("proxy_listen_host must be a non-empty address or hostname")
        if not isinstance(self.listen_ipv6, bool):
            raise ValueError("listen_ipv6 must be true or false")
        url = urlparse(self.backend_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("backend_url must use a local loopback HTTP endpoint")
        if url.path not in {"", "/"} or url.username or url.query or url.fragment:
            raise ValueError("backend_url must contain only scheme, loopback host and port")
        if self.max_concurrency != 1:
            raise ValueError("This version supports max_concurrency=1 only")
        for name in ("listen_port", "proxy_listen_port"):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= 65535:
                raise ValueError("Invalid " + name)
        if len({url.port or 80, self.listen_port, self.proxy_listen_port}) != 3:
            raise ValueError("Backend, direct and proxy listeners must use different ports")
        for name in ("request_deadline_seconds", "upstream_read_timeout_seconds",
                     "max_queue_size", "max_queue_bytes", "queue_timeout_seconds", "max_queue_timeout_seconds",
                     "health_interval_seconds", "health_timeout_seconds", "max_body_bytes",
                     "max_response_bytes", "client_write_timeout_seconds", "shutdown_grace_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(name + " must be positive")
        if self.queue_timeout_seconds > self.max_queue_timeout_seconds:
            raise ValueError("Default queue timeout exceeds maximum")


class InstanceLock:
    """OS-owned lock is automatically released after a crash, unlike a PID file."""
    def __init__(self, directory):
        import msvcrt
        self.handle = open(Path(directory) / "instance.lock", "a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            raise RuntimeError("Another gateway or maintenance process owns this data directory")

    def close(self):
        import msvcrt
        self.handle.seek(0)
        msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        self.handle.close()


class Journal:
    def __init__(self, directory):
        self.db = sqlite3.connect(str(Path(directory) / "requests.sqlite3"), timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, model TEXT NOT NULL, endpoint TEXT NOT NULL,
            state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, reason TEXT)""")
        self.db.commit()

    def recover(self):
        self.db.execute("UPDATE requests SET state='unknown', reason='gateway_restarted' WHERE state='running'")
        self.db.execute("UPDATE requests SET state='aborted', ended_at=?, reason='queued_request_lost_on_restart' WHERE state='queued'", (utc(),))
        self.db.commit()

    def enqueue(self, model, endpoint):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            counts = dict(self.db.execute("SELECT state,COUNT(*) FROM requests WHERE state IN ('running','unknown') GROUP BY state"))
            if counts.get("unknown", 0):
                self.db.rollback()
                return None, "unknown"
            ident = str(uuid.uuid4())
            self.db.execute("INSERT INTO requests(id,model,endpoint,state,started_at) VALUES(?,?,?,'queued',?)",
                            (ident, model, endpoint, utc()))
            self.db.commit()  # Durable BEFORE any request can reach Ollama.
            return ident, None
        except BaseException:
            self.db.rollback()
            raise

    def start(self, ident):
        self.db.execute("UPDATE requests SET state='running' WHERE id=? AND state='queued'", (ident,))
        self.db.commit()  # Crash after this commit is uncertain, never false idle.

    def finish(self, ident, state, reason):
        self.db.execute("UPDATE requests SET state=?, ended_at=?, reason=? WHERE id=? AND state IN ('running','queued')",
                        (state, utc(), reason, ident))
        self.db.commit()
        LOG.info("request_finished id=%s state=%s reason=%s", ident, state, reason)

    def snapshot(self):
        rows = [dict(row) for row in self.db.execute(
            "SELECT id,model,endpoint,state,started_at,reason FROM requests WHERE state IN ('running','unknown','queued') ORDER BY started_at")]
        return rows

    def prune(self):
        # Retain at most 2,000 terminal records. Never remove uncertain/active work.
        self.db.execute("""DELETE FROM requests WHERE id IN (
            SELECT id FROM requests WHERE state NOT IN ('running','unknown','queued')
            ORDER BY started_at DESC LIMIT -1 OFFSET 2000)""")
        self.db.commit()

    def close(self):
        self.db.close()


class Channel:
    """Bounded output queue; a disconnected/slow client never cancels inference."""
    def __init__(self, write_timeout):
        self.queue = asyncio.Queue(maxsize=16)
        self.detached = asyncio.Event()
        self.finished = asyncio.Event()
        self.headers = asyncio.get_running_loop().create_future()
        self.write_timeout = write_timeout

    async def send(self, chunk):
        if self.detached.is_set():
            return
        putting = asyncio.create_task(self.queue.put(chunk))
        detached = asyncio.create_task(self.detached.wait())
        try:
            done, _ = await asyncio.wait({putting, detached}, timeout=self.write_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                self.detached.set()
        finally:
            for task in (putting, detached):
                if not task.done():
                    task.cancel()
            await asyncio.gather(putting, detached, return_exceptions=True)

    async def body(self):
        try:
            while True:
                if self.detached.is_set():
                    break
                if self.finished.is_set() and self.queue.empty():
                    break
                try:
                    yield await asyncio.wait_for(self.queue.get(), 0.25)
                except asyncio.TimeoutError:
                    continue
        finally:
            self.detached.set()


class TerminalDetector:
    def __init__(self, openai=False):
        self.openai = openai
        self.buffer = b""
        self.terminal = False
        self.error = False

    def line(self, line):
        line = line.strip()
        if not line:
            return
        if self.openai:
            if not line.startswith(b"data:"):
                return
            line = line[5:].strip()
            if line == b"[DONE]":
                self.terminal = True
                return
        data = json.loads(line)
        if isinstance(data, dict):
            if data.get("error") is not None:
                self.error = self.terminal = True
            elif self.openai and data.get("type") in {"response.completed", "response.failed", "response.incomplete", "message_stop"}:
                self.terminal = True
                self.error = data["type"] in {"response.failed", "response.incomplete"}
            elif not self.openai and data.get("done") is True:
                self.terminal = True

    def feed(self, chunk):
        self.buffer += chunk
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            self.line(line)
        if len(self.buffer) > 8 * 1024 * 1024:
            raise ValueError("Upstream response line exceeded limit")

    def eof(self):
        if self.buffer.strip():
            self.line(self.buffer)


def create_app(settings: Settings):
    settings.validate()
    settings.backend_url = settings.backend_url.rstrip("/")
    pending = deque()
    transfers = {}
    wake = asyncio.Event()
    health = {"online": False, "checked_at": None, "last_success": 0.0}
    runtime = {"closing": False, "journal_ok": True, "queued_bytes": 0, "active": None}

    async def probe():
        try:
            r = await app.state.probe_client.get("/api/version", timeout=settings.health_timeout_seconds)
            value = r.json()
            health["online"] = r.status_code == 200 and isinstance(value, dict) and isinstance(value.get("version"), str)
            if health["online"]:
                health["last_success"] = time.monotonic()
        except (httpx.HTTPError, ValueError):
            health["online"] = False
        health["checked_at"] = utc()

    async def health_loop():
        while True:
            await probe()
            await asyncio.sleep(settings.health_interval_seconds)

    @asynccontextmanager
    async def lifespan(app):
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        lock = InstanceLock(settings.data_dir)
        journal = Journal(settings.data_dir)
        app.state.journal = journal
        journal.recover()
        journal.prune()
        async with httpx.AsyncClient(base_url=settings.backend_url, trust_env=False,
                                     headers={"Accept-Encoding": "identity"}) as probe_client, \
                   httpx.AsyncClient(base_url=settings.backend_url, trust_env=False,
                                     headers={"Accept-Encoding": "identity"},
                                     timeout=httpx.Timeout(connect=5, read=settings.upstream_read_timeout_seconds,
                                                           write=30, pool=5)) as client:
            app.state.client = client
            app.state.probe_client = probe_client
            async def delete_model(model, origin):
                port = settings.proxy_listen_port if origin == "remote" else settings.listen_port
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url=f"http://127.0.0.1:{port}", trust_env=False) as local:
                    return await local.request("DELETE", "/api/delete", json={"model": model},
                        headers={"X-Ollama-Queue-Timeout": str(settings.max_queue_timeout_seconds)})
            app.state.model_jobs = ModelJobs(settings.data_dir, client, delete_model, transfers)
            await probe()
            checking = asyncio.create_task(health_loop())
            dispatcher = asyncio.create_task(dispatch())
            app.state.model_jobs.start()
            app.state.manage = ManageService(app, settings)
            try:
                yield
            finally:
                runtime["closing"] = True
                await app.state.manage.close()
                await app.state.model_jobs.close()
                checking.cancel()
                await asyncio.gather(checking, return_exceptions=True)
                for job in list(pending):
                    reject_queued(job, 503, "gateway_stopping")
                wake.set()
                try:
                    await asyncio.wait_for(asyncio.shield(dispatcher), settings.shutdown_grace_seconds)
                except asyncio.TimeoutError:
                    dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)
                journal.close()
                lock.close()

    app = FastAPI(title="Ollama Status Service", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(DiscoveryHeaders)

    def online():
        return health["online"] and time.monotonic() - health["last_success"] < (
            settings.health_interval_seconds + settings.health_timeout_seconds + 2)

    @app.get("/healthz")
    async def healthz(request: Request):
        return {"ok": True, "service": "OllamaStatusService", "version": VERSION,
                "request_origin": client_origin(request, settings.proxy_listen_port)}

    @app.get("/help")
    @app.get("/.well-known/ollama-status-service")
    async def help_info(request: Request):
        return JSONResponse({
            "service": "OllamaStatusService", "version": VERSION, "help_schema_version": "1.2",
            "request_origin": client_origin(request, settings.proxy_listen_port),
            "description": "Ollama 兼容代理、状态查询和客户端可选限时排队。所有 URL 均相对于本次响应的服务器地址。",
            "urls": {"ollama_base": "/", "status": "/status", "dashboard": "/dashboard",
                     "manage": "/manage", "manage_api": "/manage/api", "model_jobs": "/oss/model-jobs",
                     "health": "/healthz", "help": "/help", "openapi": "/openapi.json",
                     "installed_models": "/api/tags", "loaded_models": "/api/ps"},
            "status_fields": {
                "state": {"idle": "无执行、排队或模型传输", "busy": "正在执行、排队或传输模型；不等于拒绝新请求",
                          "offline": "Ollama 后端不可用", "unknown": "之前任务完成情况不确定，暂停接收",
                          "stopping": "正在停止"},
                "can_start_now": "true 表示此刻可立即开始；仅为快照，实际提交仍可能被其他请求抢先",
                "accepting_requests": "true 表示可以尝试提交或排队，仍受该请求大小和队列竞争约束",
                "inflight_requests": "正在执行的请求数", "queued_requests": "尚未转交 Ollama 的请求数",
                "queued_local_requests": "等待中的本机请求数，优先获得下一个执行位置",
                "queued_remote_requests": "等待中的远端请求数",
                "active_origin": "当前执行来源 local / remote；无运行请求时为 null",
                "uncertain_requests": "不能确认已完成的请求数",
                "inflight_model_transfers": "当前 pull/push HTTP 请求数；单独统计，不占推理队列",
                "model_transfers": "当前模型传输的 ID、接口、来源和开始时间；不代表后端全部下载活动",
                "loaded_models_note": "/api/ps 表示模型驻留内存，不代表正在推理"},
            "queue_policy": {
                "header": "X-Ollama-Queue-Timeout", "unit": "seconds", "type": "number",
                "minimum": 0, "maximum": settings.max_queue_timeout_seconds,
                "default": settings.queue_timeout_seconds,
                "zero": "忙时立即返回 HTTP 429 busy_no_queue；客户端可选择其他服务器",
                "positive": "最多排队 N 秒；本机优先，两类各自 FIFO；开始执行后排队计时结束，不中断推理",
                "timeout": "HTTP 429 queue_wait_timeout，forwarded=false，可转向其他服务器",
                "ordering": "local_first_fifo_per_origin",
                "priority": {
                    "preempt_running": False, "reservation_supported": False,
                    "local_detection": "Proxy listener is always remote. On the direct listener only: socket peer is loopback (including IPv4-mapped IPv6), or equals the local socket address. HTTP headers are ignored.",
                    "recommended_local_base": f"http://127.0.0.1:{settings.listen_port}",
                    "proxy_listener": {"host": settings.proxy_listen_host, "port": settings.proxy_listen_port,
                                       "origin": "remote", "shared_queue": True,
                                       "detection": "Accepted socket local port; never HTTP headers",
                                       "migration": "Point FRP/reverse-proxy upstreams to this listener, not the direct listener"},
                    "rule": "Finish the active request, then oldest waiting local request, otherwise oldest waiting remote request",
                    "no_hold_between_requests": True,
                    "zero_timeout_applies_to_local": True,
                    "caveat": "A local request must actually enter the queue and remain within its deadline. Busy probes and zero-wait refusals do not reserve priority. Continuous local work may delay remote work until its queue timeout."},
                "max_waiting_requests": settings.max_queue_size,
                "max_waiting_body_bytes": settings.max_queue_bytes,
                "restart": "排队内容不自动重放；已开始但未确认完成的任务进入 unknown",
                "client_disconnect": "排队中断开则移除；执行中断开则继续跟踪直到完成或 unknown"},
            "manage_policy": {"authentication": "existing_reverse_proxy", "separate_login": False,
                "registry": "per_OSS_server", "execution": "target_OSS_server", "csrf_header": "X-OSS-Token"},
            "model_job_policy": {"schema": 1, "endpoint": "/oss/model-jobs", "kinds": ["pull", "delete"],
                "controls": ["pause", "resume"], "paused_restart": "Paused pulls remain paused after restart.",
                "execution": "server_owned", "client_disconnect": "Continues on this OSS server, including when the client computer shuts down.",
                "restart": "Pulls resume on OSS restart; unconfirmed deletes are never automatically replayed.",
                "request_key": "Required stable idempotency key; reuse the same key after an uncertain submission."},
            "model_transfer_policy": {
                "endpoints": sorted(MODEL_TRANSFERS), "method": "POST",
                "scheduling": "concurrent_passthrough", "uses_inference_queue": False,
                "queue_header_applies": False, "inference_deadlines_apply": False,
                "status_note": "Active transfer connections make state busy but do not consume inference slots; use can_start_now for inference readiness.",
                "client_disconnect": "Close the upstream HTTP connection; Ollama owns download cancellation and resume behavior.",
                "tracking": "Live HTTP connections only; no replay or persistent inference recovery record."},
            "client_profiles": {
                "switch_when_busy": {"headers": {"X-Ollama-Queue-Timeout": "0"}},
                "wait_then_switch": {"headers": {"X-Ollama-Queue-Timeout": str(min(30, settings.max_queue_timeout_seconds))}},
                "legacy_client": {"headers": {}, "effective_wait_seconds": settings.queue_timeout_seconds}},
            "safe_failover": {
                "allowed_http_codes": [429, 503],
                "required_response_header": {"X-Ollama-Forwarded": "false"},
                "required_json_field": {"forwarded": False},
                "rule": "只有服务器明确确认本次请求未转交 Ollama 时，才可自动把同一请求发往下一台",
                "never_replay_automatically": ["POST 网络超时或断线", "X-Ollama-Forwarded: true", "已输出部分结果"],
                "status_probe_is_reservation": False,
                "server_selects_other_servers": False},
            "limits": {"max_concurrency": 1, "max_json_body_bytes": settings.max_body_bytes,
                       "execution_deadline_seconds": settings.request_deadline_seconds,
                       "upstream_read_timeout_seconds": settings.upstream_read_timeout_seconds,
                       "client_timeout_advice": "客户端响应超时应覆盖所选排队时间、执行时限及网络余量；超时不能当成安全重试信号"},
            "configuration": {
                "client_change_method": "按请求设置 X-Ollama-Queue-Timeout，无需修改服务器或重启",
                "server_change_method": "仅 A 上的管理员编辑 %ProgramData%\\OllamaStatusService\\config.json 并重启服务",
                "remote_configuration_api": False,
                "server_fields": ["queue_timeout_seconds", "max_queue_timeout_seconds", "max_queue_size", "max_queue_bytes",
                                  "listen_host", "listen_ipv6", "listen_port", "proxy_listen_host", "proxy_listen_port"]},
            "examples": [{"method": "GET", "path": "/status"},
                         {"method": "POST", "path": "/api/generate",
                          "headers": {"Content-Type": "application/json", "X-Ollama-Queue-Timeout": "0"},
                          "json": {"model": "REPLACE_WITH_INSTALLED_MODEL", "prompt": "你好", "stream": False}}]
        }, headers={"Cache-Control": "no-store"})

    @app.get("/openapi.json")
    async def api_schema():
        parameter = {"name": "X-Ollama-Queue-Timeout", "in": "header", "required": False,
                     "description": "0: reject immediately when busy; N: wait at most N seconds before execution.",
                     "schema": {"type": "number", "minimum": 0, "maximum": settings.max_queue_timeout_seconds,
                                "default": settings.queue_timeout_seconds}}
        paths = {path: {"get": {"summary": title, "responses": {"200": {"description": title}}}}
                 for path, title in [("/help", "Machine-readable capabilities and routing rules"),
                                     ("/status", "Current status snapshot"), ("/healthz", "Gateway liveness"),
                                     ("/api/tags", "Installed models"), ("/api/ps", "Loaded models")]}
        paths["/oss/model-jobs"] = {
            "get": {"summary": "List server-owned model jobs", "responses": {"200": {"description": "Active and recent jobs"}}},
            "post": {"summary": "Submit a durable server-owned model task", "requestBody": {"required": True, "content": {
                "application/json": {"schema": {"type": "object", "required": ["kind", "model", "request_key"], "properties": {
                    "kind": {"enum": ["pull", "delete"]}, "model": {"type": "string"}, "request_key": {"type": "string", "minLength": 16, "maxLength": 128},
                    "confirm_model": {"type": "string", "description": "Required for delete; must match model"}}}}}},
                "responses": {"202": {"description": "Persisted task; client can disconnect"}, "409": {"description": "Conflicting operation or request key"}}}}
        paths["/oss/model-jobs/{id}"] = {"get": {"summary": "Query a persistent model task", "parameters": [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "Current job"}, "404": {"description": "Unknown job"}}}}
        for action in ("pause", "resume"):
            paths["/oss/model-jobs/{id}/" + action] = {"post": {"summary": action.title() + " a server-owned pull",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Persisted task state"}, "409": {"description": "Task state does not allow this action"}}}}
        for path in sorted(INFERENCE):
            paths[path] = {"post": {"summary": "Ollama-compatible inference with optional bounded queue",
                "parameters": [parameter], "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"type": "object", "required": ["model"],
                        "properties": {"model": {"type": "string"}, "stream": {"type": "boolean"}},
                        "additionalProperties": True}}}},
                "responses": {"200": {"description": "Original backend result/stream"},
                              "400": {"description": "Invalid request or queue parameter"},
                              "429": {"description": "No-queue refusal, queue full or queue timeout; forwarded=false"},
                              "503": {"description": "Unavailable; check X-Ollama-Forwarded before retry"}}}}
        return {"openapi": "3.1.0", "info": {"title": "Ollama Status Service", "version": VERSION,
                "description": "Gateway extension schema. Model-specific request fields follow the installed Ollama API."},
                "servers": [{"url": "/"}], "paths": paths}

    @app.get("/status")
    async def status():
        try:
            rows = app.state.journal.snapshot()
        except sqlite3.Error:
            runtime["journal_ok"] = False
            rows = []
        uncertain = sum(r["state"] == "unknown" for r in rows)
        queued = sum(r["state"] == "queued" for r in rows)
        inflight = sum(r["state"] == "running" for r in rows)
        origins = {job["id"]: job["origin"] for job in pending}
        active = runtime["active"]
        if active:
            origins[active["id"]] = active["origin"]
        rows = [dict(row, origin=origins.get(row["id"], "unknown")) for row in rows]
        backend_online = online()
        model_jobs = app.state.model_jobs.jobs()
        active_model_jobs = sum(j["state"] in {"queued", "running"} for j in model_jobs)
        state = ("unknown" if uncertain or not runtime["journal_ok"] else
                 "offline" if not backend_online else "busy" if inflight or queued or transfers or active_model_jobs else
                 "stopping" if runtime["closing"] else "idle")
        return JSONResponse({"service": "OllamaStatusService", "version": VERSION,
                             "state": state, "ollama_online": backend_online,
                             "accepting_requests": state in {"idle", "busy"} and queued < settings.max_queue_size
                             and runtime["queued_bytes"] < settings.max_queue_bytes and not runtime["closing"], "inflight_requests": inflight,
                             "can_start_now": state in {"idle", "busy"} and not inflight and not queued and not runtime["closing"],
                             "inflight_model_transfers": len(transfers), "model_transfers": list(transfers.values()),
                             "model_jobs": {"schema": 1, "endpoint": "/oss/model-jobs", "execution": "server_owned", "active": active_model_jobs, "controls": ["pause", "resume"]},
                             "uncertain_requests": uncertain, "queued_requests": queued,
                             "queued_local_requests": sum(job["origin"] == "local" for job in pending),
                             "queued_remote_requests": sum(job["origin"] == "remote" for job in pending),
                             "active_origin": active["origin"] if active else None,
                             "scheduling_policy": "local_first_fifo_per_origin",
                             "max_queue_size": settings.max_queue_size,
                             "queued_body_bytes": runtime["queued_bytes"],
                             "max_queue_bytes": settings.max_queue_bytes,
                             "queue_timeout_seconds": settings.queue_timeout_seconds,
                             "max_queue_timeout_seconds": settings.max_queue_timeout_seconds,
                             "max_concurrency": 1, "requests": rows,
                             "backend_checked_at": health["checked_at"], "checked_at": utc()},
                            headers={"Cache-Control": "no-store"})

    @app.get("/dashboard", response_class=HTMLResponse)
    async def home():
        return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")

    async def read_body(request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_body_bytes:
                raise OverflowError("Request body too large")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    # Strip hop-by-hop headers. Preserve authentication, CORS and API headers.
    def headers_for(headers):
        excluded = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                    "te", "trailer", "transfer-encoding", "upgrade", "content-length", "content-encoding"}
        excluded.update(x.strip().lower() for x in headers.get("connection", "").split(","))
        return {k: v for k, v in headers.items() if k.lower() not in excluded}

    def target(request):
        # Relative path prevents a client-supplied path from replacing the backend host.
        raw = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
        return "./" + raw.lstrip("/") + ("?" + request.url.query if request.url.query else "")

    async def passthrough(request, model_transfer=False):
        content = request.stream()
        if model_transfer:
            # Retain the existing JSON request-size bound, without rewriting the
            # body. Once consumed, disconnect checks cannot steal request bytes.
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > settings.max_body_bytes:
                    return JSONResponse({"error": "Request body too large"}, status_code=413)
            content = bytes(content)
        ident = str(uuid.uuid4()) if model_transfer else None
        if ident:
            if runtime["closing"]:
                return JSONResponse({"error": "Gateway stopping"}, status_code=503)
            transfers[ident] = {"id": ident, "endpoint": request.url.path.rstrip("/"),
                                "origin": client_origin(request, settings.proxy_listen_port), "started_at": utc()}
        try:
            # Transfers may be large or quiet during verification. Preserve Ollama's
            # stream without inference queue/read/total deadlines; connect/write/pool
            # failures still have finite transport timeouts.
            options = {"timeout": httpx.Timeout(connect=5, read=None, write=30, pool=5)} if model_transfer else {}
            req = app.state.client.build_request(request.method, target(request),
                content=content, headers=headers_for(request.headers), **options)
            if model_transfer:
                sending = asyncio.create_task(app.state.client.send(req, stream=True))
                received = False
                try:
                    while not sending.done():
                        if await request.is_disconnected():
                            transfers.pop(ident, None)
                            return Response(status_code=499)
                        await asyncio.wait({sending}, timeout=.1)
                    upstream = sending.result()
                    received = True
                finally:
                    if not received:
                        sending.cancel()
                        with anyio.CancelScope(shield=True):
                            result = (await asyncio.gather(sending, return_exceptions=True))[0]
                            if isinstance(result, httpx.Response):
                                await result.aclose()
            else:
                upstream = await app.state.client.send(req, stream=True)
        except httpx.HTTPError:
            transfers.pop(ident, None)
            return JSONResponse({"error": "Ollama backend unavailable"}, status_code=503)
        except BaseException:
            transfers.pop(ident, None)
            raise

        class ProxyResponse(StreamingResponse):
            async def __call__(self, scope, receive, send):
                # Cleanup even when the client leaves before body iteration starts.
                # Shield it from Starlette's disconnect cancellation scope.
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    try:
                        with anyio.CancelScope(shield=True):
                            await upstream.aclose()
                    finally:
                        transfers.pop(ident, None)

        async def body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            except httpx.HTTPError:
                LOG.warning("Passthrough stream interrupted endpoint=%s", request.url.path)
                raise
        return ProxyResponse(body(), status_code=upstream.status_code,
                             headers=headers_for(upstream.headers))

    def finish(ident, state, reason):
        try:
            app.state.journal.finish(ident, state, reason)
            app.state.journal.prune()
        except sqlite3.Error:
            runtime["journal_ok"] = False
            LOG.exception("Journal update failed; blocking further inference")

    async def produce(ident, path, payload, channel, method, url, headers):
        final_state, reason = "unknown", "upstream_completion_not_confirmed"
        acknowledged = False
        known_inference = path in INFERENCE
        streaming = path not in EMBEDDING and payload.get("stream", not path.startswith("/v1/")) is not False
        try:
            async with asyncio.timeout(settings.request_deadline_seconds):
                async with app.state.client.stream(method, url, json=payload, headers=headers) as upstream:
                    content_type = upstream.headers.get("content-type", "application/json")
                    channel.response_headers = headers_for(upstream.headers)
                    channel.response_headers["X-Ollama-Forwarded"] = "true"
                    if upstream.status_code >= 400:
                        error_body = bytearray()
                        async for chunk in upstream.aiter_bytes():
                            error_body.extend(chunk)
                            if len(error_body) > settings.max_response_bytes:
                                raise ValueError("Upstream error response too large")
                        final_state, reason = "failed", "upstream_http_" + str(upstream.status_code)
                        channel.headers.set_result((upstream.status_code, "application/json"))
                        await channel.send(bytes(error_body))
                    elif not known_inference:
                        # Model-management operations finish when the HTTP response completes.
                        channel.headers.set_result((upstream.status_code, content_type))
                        async for chunk in upstream.aiter_bytes():
                            await channel.send(chunk)
                        acknowledged = True
                        final_state, reason = "done", "http_completed"
                    elif streaming:
                        if upstream.status_code != 200:
                            raise ValueError("Unexpected upstream status")
                        channel.headers.set_result((200, content_type))
                        detector = TerminalDetector(path.startswith("/v1/"))
                        async for chunk in upstream.aiter_bytes():
                            detector.feed(chunk)
                            acknowledged = detector.terminal
                            await channel.send(chunk)
                            if acknowledged:
                                break
                        detector.eof()
                        acknowledged = detector.terminal
                        if not acknowledged:
                            raise ValueError("Upstream stream ended without completion marker")
                        final_state = "failed" if detector.error else "done"
                        reason = "upstream_error" if detector.error else "completed"
                    else:
                        result = bytearray()
                        async for chunk in upstream.aiter_bytes():
                            result.extend(chunk)
                            if len(result) > settings.max_response_bytes:
                                raise ValueError("Upstream response too large")
                        value = json.loads(result)
                        valid = isinstance(value, dict) and (
                            value.get("done") is True or "error" in value or
                            (path == "/api/embed" and isinstance(value.get("embeddings"), list)) or
                            (path == "/api/embeddings" and isinstance(value.get("embedding"), list)) or
                            (path == "/v1/embeddings" and isinstance(value.get("data"), list)) or
                            (path == "/v1/responses" and value.get("status") in {"completed", "failed", "incomplete"}) or
                            (path == "/v1/messages" and value.get("type") == "message" and value.get("stop_reason") is not None) or
                            (path in {"/v1/chat/completions", "/v1/completions"} and isinstance(value.get("choices"), list)))
                        if not valid:
                            raise ValueError("Upstream response lacks completion evidence")
                        acknowledged = True
                        final_state = "failed" if "error" in value else "done"
                        reason = "upstream_error" if "error" in value else "completed"
                        channel.headers.set_result((upstream.status_code, content_type))
                        await channel.send(bytes(result))
        except httpx.ConnectError:
            # A refused TCP connection cannot have delivered an HTTP request.
            final_state, reason = "failed", "upstream_connect_failed"
            channel.response_headers = {"X-Ollama-Forwarded": "false"}
            if not channel.headers.done():
                channel.headers.set_result((503, "application/json"))
            await channel.send(b'{"error":"Ollama connection failed","forwarded":false}')
        except asyncio.CancelledError:
            if acknowledged:
                final_state, reason = "done", "completion_received_before_shutdown"
            else:
                reason = "gateway_shutdown_before_completion"
            raise
        except Exception as exc:
            LOG.warning("request_uncertain id=%s error=%s", ident, type(exc).__name__)
            reason = type(exc).__name__
            if acknowledged:
                final_state, reason = "done", "completion_received"
            if not channel.headers.done():
                channel.headers.set_result((502, "application/json"))
            error = {"error": "Upstream completion is unconfirmed; gateway requires recovery", "request_id": ident}
            line = json.dumps(error).encode() + b"\n"
            if streaming and path.startswith("/v1/"):
                line = b"data: " + line + b"\n"
            await channel.send(line)
        finally:
            # Only explicit completion/rejection releases admission. Ambiguity persists.
            finish(ident, final_state, reason)
            if not channel.headers.done():
                channel.headers.set_result((503, "application/json"))
            channel.finished.set()

    def remove_pending(job):
        pending.remove(job)
        runtime["queued_bytes"] -= job["size"]

    def reject_queued(job, code, reason):
        if job["state"] != "queued":
            return
        remove_pending(job)
        job["state"] = "aborted"
        finish(job["id"], "aborted", reason)
        channel = job["channel"]
        channel.response_headers = {"X-Ollama-Forwarded": "false", "Retry-After": "1"}
        if not channel.headers.done():
            channel.headers.set_result((code, "application/json"))
        channel.queue.put_nowait(json.dumps({"error": reason, "request_id": job["id"],
                                            "forwarded": False}).encode())
        channel.finished.set()

    async def dispatch():
        while not runtime["closing"]:
            await wake.wait()
            wake.clear()
            while pending and not runtime["closing"]:
                # At each execution boundary choose the oldest waiting local job.
                # Never reserve a future slot or interrupt the active producer.
                job = next((item for item in pending if item["origin"] == "local"), pending[0])
                try:
                    uncertain = any(r["state"] == "unknown" for r in app.state.journal.snapshot())
                    if uncertain or not runtime["journal_ok"] or not online():
                        reject_queued(job, 503, "backend_unavailable_or_recovery_required")
                        continue
                    if job["queue_timeout"] > 0 and time.monotonic() - job["enqueued"] >= job["queue_timeout"]:
                        reject_queued(job, 429, "queue_wait_timeout")
                        continue
                    app.state.journal.start(job["id"])
                except sqlite3.Error:
                    runtime["journal_ok"] = False
                    reject_queued(job, 503, "task_journal_unavailable")
                    continue
                remove_pending(job)
                job["state"] = "running"
                runtime["active"] = job
                LOG.info("request_started id=%s origin=%s waiting_local=%s waiting_remote=%s",
                         job["id"], job["origin"], sum(j["origin"] == "local" for j in pending),
                         sum(j["origin"] == "remote" for j in pending))
                try:
                    await produce(job["id"], job["path"], job["payload"], job["channel"],
                                  job["method"], job["url"], job["headers"])
                finally:
                    runtime["active"] = None
                    job["state"] = "finished"

    async def scheduled(request: Request):
        import math
        def decline(error, code=503):
            return JSONResponse({"error": error, "forwarded": False}, status_code=code,
                                headers={"X-Ollama-Forwarded": "false", "Retry-After": "1"})
        try:
            queue_timeout = float(request.headers.get("x-ollama-queue-timeout", settings.queue_timeout_seconds))
            if not math.isfinite(queue_timeout) or not 0 <= queue_timeout <= settings.max_queue_timeout_seconds:
                raise ValueError()
        except ValueError:
            return decline("X-Ollama-Queue-Timeout must be between 0 and " + str(settings.max_queue_timeout_seconds), 400)
        try:
            payload = await read_body(request)
        except OverflowError:
            return JSONResponse({"error": "Request body too large"}, status_code=413)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        path = request.url.path.rstrip("/")
        model = payload.get("model", payload.get("name", ""))
        if path in INFERENCE and (not isinstance(model, str) or not model.strip()):
            return JSONResponse({"error": "model is required"}, status_code=400)
        if "stream" in payload and not isinstance(payload["stream"], bool):
            return JSONResponse({"error": "stream must be boolean"}, status_code=400)
        if runtime["closing"] or not runtime["journal_ok"] or not online():
            return decline("Gateway or backend unavailable")
        try:
            rows = app.state.journal.snapshot()
        except sqlite3.Error:
            runtime["journal_ok"] = False
            return decline("Task journal unavailable")
        if any(r["state"] == "unknown" for r in rows):
            return decline("Recovery required")
        if queue_timeout == 0 and rows:
            return decline("busy_no_queue", 429)
        size = len(json.dumps(payload, ensure_ascii=False).encode())
        if len(pending) >= settings.max_queue_size or runtime["queued_bytes"] + size > settings.max_queue_bytes:
            return decline("queue_full", 429)
        try:
            ident, rejected = app.state.journal.enqueue(str(model), path)
        except sqlite3.Error:
            runtime["journal_ok"] = False
            LOG.exception("Admission journal failure")
            return decline("Task journal unavailable")
        if rejected:
            return decline("Recovery required")
        channel = Channel(settings.client_write_timeout_seconds)
        job = dict(id=ident, path=path, payload=payload, channel=channel, method=request.method,
                   url=target(request), headers=headers_for(request.headers), size=size,
                    origin=client_origin(request, settings.proxy_listen_port),
                   enqueued=time.monotonic(), state="queued", queue_timeout=queue_timeout)
        pending.append(job)
        runtime["queued_bytes"] += size
        wake.set()
        LOG.info("request_queued id=%s origin=%s model=%s endpoint=%s", ident, job["origin"], model, path)
        try:
            # Monitor disconnection while waiting for headers. Do not execute abandoned queued work.
            while not channel.headers.done():
                if await request.is_disconnected():
                    channel.detached.set()
                    reject_queued(job, 499, "client_disconnected_while_queued")
                    return Response(status_code=499)
                if job["state"] == "queued" and queue_timeout > 0 and time.monotonic() - job["enqueued"] >= queue_timeout:
                    reject_queued(job, 429, "queue_wait_timeout")
                    break
                await asyncio.wait({channel.headers}, timeout=.1)
            status_code, content_type = channel.headers.result()
        except BaseException:
            channel.detached.set()
            reject_queued(job, 503, "request_cancelled_while_queued")
            raise
        response_headers = dict(getattr(channel, "response_headers", {}))
        response_headers.setdefault("X-Ollama-Forwarded", "true")
        response_headers.update({"X-Request-ID": ident, "Cache-Control": "no-store"})
        return StreamingResponse(channel.body(), status_code=status_code, media_type=content_type,
                                 headers=response_headers)

    @app.get("/oss/model-jobs")
    async def list_model_jobs():
        jobs = app.state.model_jobs.jobs()
        active = [j for j in jobs if j["state"] in {"queued", "running", "paused"}]
        recent = [j for j in jobs if j["state"] not in {"queued", "running", "paused"}][:200]
        return JSONResponse({"schema": 1, "execution": "server_owned", "controls": ["pause", "resume"], "jobs": active + recent}, headers={"Cache-Control": "no-store"})

    @app.post("/oss/model-jobs/{ident}/{action}")
    async def control_model_job(ident: str, action: str):
        try:
            job = await app.state.model_jobs.control(ident, action)
            return JSONResponse(job, headers={"Cache-Control": "no-store"})
        except JobError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.get("/oss/model-jobs/{ident}")
    async def get_model_job(ident: str):
        try:
            return JSONResponse(app.state.model_jobs.get(ident), headers={"Cache-Control": "no-store"})
        except JobError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.post("/oss/model-jobs")
    async def submit_model_job(request: Request):
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192:
                    raise JobError("Request too large", 413)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise JobError("Expected a JSON object")
            job = app.state.model_jobs.submit(payload, client_origin(request, settings.proxy_listen_port))
            return JSONResponse(job, status_code=202, headers={"Cache-Control": "no-store"})
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        except JobError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)

    install_routes(app, VERSION)

    @app.api_route("/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(request: Request, rest: str):
        path = request.url.path.rstrip("/")
        if request.method == "POST" and path in MODEL_TRANSFERS:
            return await passthrough(request, model_transfer=True)
        if request.method in {"GET", "HEAD", "OPTIONS"} or path in {"/api/show", "/v1/messages/count_tokens"} or path.startswith("/api/blobs/"):
            return await passthrough(request)
        return await scheduled(request)

    return app
