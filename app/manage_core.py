"""Embedded multi-server manager; no listener, process or login of its own."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import io
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parent
ACTIVE = {"queued", "running"}
SELF_ID = "00000000000000000000000000000001"


def now():
    return datetime.now(timezone.utc).isoformat()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def protect(value: str, decrypt=False):
    """Windows DPAPI, bound to the current Windows account; never plaintext fallback."""
    if not value:
        return ""
    if os.name != "nt":
        raise ApiError("认证信息的保存需要 Windows DPAPI。")
    raw = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(source), "OSS Server", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ApiError("无法读取或保存凭据，请使用原 Windows 账户，或重新填写认证。")
    try:
        data = ctypes.string_at(target.pbData, target.cbData)
        return data.decode("utf-8") if decrypt else base64.b64encode(data).decode("ascii")
    finally:
        kernel.LocalFree(target.pbData)


def normalize_url(value):
    try:
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError()
        p = urlsplit(value.rstrip("/"))
        if p.scheme not in {"http", "https"} or not p.hostname or p.query or p.fragment or "\\" in value:
            raise ValueError()
        if p.port is not None and not 1 <= p.port <= 65535:
            raise ValueError()
        if p.netloc.endswith(":") or any(x in {"..", "."} for x in unquote(p.path).split("/")):
            raise ValueError()
        host = p.hostname.lower().encode("idna").decode("ascii")
        host = f"[{host}]" if ":" in host else host
        default_port = 443 if p.scheme == "https" else 80
        netloc = host + (f":{p.port}" if p.port and p.port != default_port else "")
        credentials = None
        if "@" in p.netloc:
            info = p.netloc.rsplit("@", 1)[0]
            if ":" not in info or "@" in info or re.search(r"%(?![0-9a-fA-F]{2})", info):
                raise ValueError()
            user, password = [unquote(x, errors="strict") for x in info.split(":", 1)]
            validate_credentials(user, password)
            credentials = {"username": user, "password": password}
        return p._replace(netloc=netloc).geturl(), credentials
    except (ValueError, UnicodeError):
        raise ApiError("请输入有效的 http:// 或 https:// 服务器地址；地址中的特殊密码字符需要 URL 编码。") from None


def validate_credentials(user, password):
    if not isinstance(user, str) or not isinstance(password, str) or not user or ":" in user:
        raise ApiError("Basic 认证需要用户名；用户名不能包含冒号。")
    if any(ord(c) < 32 or ord(c) == 127 for c in user + password):
        raise ApiError("认证信息不能包含控制字符。")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def credentials(server):
    return json.loads(protect(server["secret"], decrypt=True)) if server.get("secret") else {}


def redact(message, server):
    text = str(message)
    try:
        auth = credentials(server)
        values = [auth.get("password", "")]
        if auth:
            values.append(base64.b64encode((auth["username"] + ":" + auth["password"]).encode()).decode())
        for value in sorted(filter(None, values), key=len, reverse=True):
            text = text.replace(value, "[已隐藏]")
    except Exception:
        pass
    return re.sub(r"(https?://)[^\s/]*@", r"\1", text)[:600]


def upstream(server, path, method="GET", payload=None, timeout=5, queue=False):
    if server.get("_local_request"):
        status, data = server["_local_request"](method, path, payload, timeout)
        response = io.BytesIO(data)
        if status >= 400:
            raise HTTPError(path, status, "OSS request failed", {}, response)
        return response
    headers = {"Accept": "application/json", "User-Agent": "OSS-Server-Manager/1.0"}
    auth = credentials(server)
    if auth:
        token = base64.b64encode((auth["username"] + ":" + auth["password"]).encode()).decode()
        headers["Authorization"] = "Basic " + token
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode()
    if queue:
        headers["X-Ollama-Queue-Timeout"] = "3600"
    req = Request(server["url"] + path, data=body, headers=headers, method=method)
    return build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=timeout)


def read_json(server, path, timeout=4):
    with upstream(server, path, timeout=timeout) as response:
        data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ApiError("服务器响应过大。", 502)
        return json.loads(data)


def error_text(exc, server):
    if isinstance(exc, HTTPError):
        mapping = {401: "认证失败，请检查用户名和密码", 403: "服务器拒绝访问", 404: "接口或模型不存在",
                   429: "服务器队列已满或等待超时", 503: "服务器暂不可用或正在维护"}
        message = mapping.get(exc.code, "服务器请求失败")
        try:
            body = json.loads(exc.read(65536))
            detail = body.get("error", "")
            if detail:
                message += "：" + str(detail)
        except Exception:
            pass
        return redact(f"HTTP {exc.code} · {message}", server)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "连接或读取超时；操作结果可能尚未确认，请刷新模型列表核对。"
    if isinstance(exc, URLError):
        return "无法连接服务器，请检查地址、网络及 HTTPS 证书。"
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "服务器返回了无效数据。"
    return redact(str(exc) or "请求失败", server)


class Store:
    def __init__(self, folder, local_request=None):
        self.local_request = local_request
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "manager.sqlite3"
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS servers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL, secret TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()
        for job in self.jobs():
            if job["state"] in ACTIVE and job.get("execution") != "remote":
                job.update(state="interrupted", phase="管理器已重启", error="上次任务的结果未确认，请刷新模型列表核对后再操作。", updated=now())
                self.save_job(job)

    def servers(self):
        with self.lock:
            return [self.decorate(dict(x)) for x in self.db.execute("SELECT * FROM servers ORDER BY created, rowid")]

    def get(self, ident):
        with self.lock:
            row = self.db.execute("SELECT * FROM servers WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ApiError("服务器不存在。", 404)
        return self.decorate(dict(row))

    def decorate(self, row):
        if row["id"] == SELF_ID:
            row["_local_request"] = self.local_request
        return row

    def save_server(self, data):
        with self.lock:
            try:
                self.db.execute("""INSERT INTO servers VALUES (:id,:name,:url,:enabled,:secret,:created)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
                    enabled=excluded.enabled,secret=excluded.secret""", data)
                self.db.commit()
            except sqlite3.IntegrityError:
                self.db.rollback()
                raise ApiError("这个服务器地址已经存在。", 409) from None

    def remove(self, ident):
        with self.lock:
            self.db.execute("DELETE FROM servers WHERE id=?", (ident,))
            self.db.commit()

    def jobs(self):
        with self.lock:
            return [json.loads(x[0]) for x in self.db.execute("SELECT payload FROM jobs ORDER BY rowid DESC")]

    def save_job(self, job):
        with self.lock:
            self.db.execute("INSERT INTO jobs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                            (job["id"], json.dumps(job, ensure_ascii=False)))
            self.db.commit()

    def meta(self, key, value=None):
        with self.lock:
            if value is not None:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
                self.db.commit()
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None


class Manager:
    def __init__(self, folder, local_request, polling=True):
        self.store = Store(folder, local_request)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.snapshots = {}
        self.probing = set()
        self.stopping = threading.Event()
        self.probes = ThreadPoolExecutor(max_workers=10, thread_name_prefix="manage-probe")
        self.store.save_server(dict(id=SELF_ID, name="本服务器 · " + socket.gethostname(),
            url="http://oss.internal", enabled=1, secret="", created=now()))
        self.poller = None
        if polling:
            self.poller = threading.Thread(target=self.poll, name="manage-poll", daemon=True)
            self.poller.start()

    def public_server(self, row):
        return {"id": row["id"], "name": row["name"], "url": row["url"],
                "enabled": bool(row["enabled"]), "has_auth": bool(row["secret"]), "is_self": row["id"] == SELF_ID}

    def import_servers(self, payload):
        entries = payload.get("servers")
        if not isinstance(entries, list) or not entries or len(entries) > 100:
            raise ApiError("请选择包含 1–100 台服务器的配置文件。")
        prepared = []
        for item in entries:
            if not isinstance(item, dict):
                raise ApiError("服务器配置格式无效。")
            name = item.get("name", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ApiError("服务器名称需要 1–80 个字符。")
            address = item.get("url")
            if not isinstance(address, str):
                raise ApiError("服务器地址无效。")
            url, embedded_auth = normalize_url(address)
            auth = item.get("auth") or embedded_auth or {}
            if not isinstance(auth, dict):
                raise ApiError("服务器认证格式无效。")
            if auth:
                validate_credentials(auth.get("username"), auth.get("password"))
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ApiError("启用状态无效。")
            prepared.append(dict(id=uuid.uuid4().hex, name=name.strip(), url=url, enabled=int(enabled),
                secret=protect(json.dumps(auth)) if auth else "", created=now()))
        added, skipped = 0, 0
        with self.lock:
            existing = {s["url"] for s in self.store.servers()}
            unique = {s["url"] for s in prepared} - existing
            if len(existing) + len(unique) > 101:
                raise ApiError("最多保存 100 台其他服务器。", 409)
            for row in prepared:
                if row["url"] in existing:
                    skipped += 1
                else:
                    self.store.save_server(row)
                    existing.add(row["url"])
                    added += 1
        self.refresh()
        return {"added": added, "skipped": skipped}

    def update_server(self, payload, ident=None):
        with self.lock:
            if ident == SELF_ID:
                raise ApiError("当前 OSS 节点由服务自动管理，不能修改或停用。", 409)
            if not ident and len(self.store.servers()) >= 101:
                raise ApiError("最多保存 100 台其他服务器。", 409)
            old = self.store.get(ident) if ident else None
            if old and self.has_active(ident):
                raise ApiError("该服务器有进行中或暂停的任务，完成后才能修改。", 409)
            name = payload.get("name", "").strip() if isinstance(payload.get("name"), str) else ""
            if not name or len(name) > 80:
                raise ApiError("请填写 1–80 字的服务器名称。")
            address = payload.get("url", "")
            if not isinstance(address, str):
                raise ApiError("服务器地址必须是文本。")
            url, auth = normalize_url(address.strip())
            if not isinstance(payload.get("enabled", True), bool):
                raise ApiError("启用状态无效。")
            mode = payload.get("auth_mode", "keep" if old else "none")
            if mode not in {"keep", "none", "basic"}:
                raise ApiError("认证方式无效。")
            if mode == "basic":
                user, password = payload.get("username", ""), payload.get("password", "")
                validate_credentials(user, password)
                auth = {"username": user, "password": password}
            if auth:
                secret = protect(json.dumps(auth))
            elif mode == "keep" and old:
                if url != old["url"] and old["secret"]:
                    raise ApiError("更换地址时请重新填写认证，或选择无需认证。")
                secret = old["secret"]
            else:
                secret = ""
            row = {"id": ident or uuid.uuid4().hex, "name": name, "url": url,
                   "enabled": int(payload.get("enabled", True)), "secret": secret,
                   "created": old["created"] if old else now()}
            self.store.save_server(row)
            self.snapshots.pop(row["id"], None)
        self.refresh(row["id"])
        return self.public_server(row)

    def has_active(self, ident):
        return any(j["server_id"] == ident and j["state"] in ACTIVE | {"paused"} for j in self.store.jobs())

    def remove_server(self, ident):
        if ident == SELF_ID:
            raise ApiError("当前 OSS 节点不能移除。", 409)
        with self.lock:
            self.store.get(ident)
            if self.has_active(ident):
                raise ApiError("该服务器有进行中或暂停的任务，完成后才能移除。", 409)
            self.store.remove(ident)
            self.snapshots.pop(ident, None)

    def probe(self, row):
        snapshot = {"online": False, "state": "offline", "models": [], "checked_at": now(), "error": ""}
        try:
            tags = read_json(row, "/api/tags")
            if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
                raise ValueError()
            snapshot.update(online=True, state="online", models=tags["models"], kind="Ollama", version="", loaded=[], warnings=[])
            try:
                status = read_json(row, "/status")
                if isinstance(status, dict) and status.get("service") == "OllamaStatusService":
                    snapshot.update(kind="OSS", version=status.get("version", ""), state=status.get("state", "unknown"),
                                    active=status.get("inflight_requests", 0), queued=status.get("queued_requests", 0),
                                    transfers=status.get("inflight_model_transfers", 0))
                    capability = status.get("model_jobs", {})
                    snapshot["remote_jobs"] = capability.get("schema") == 1 and capability.get("execution") == "server_owned"
                    snapshot["job_controls"] = [a for a in ("pause", "resume") if a in capability.get("controls", [])]
                    if snapshot["remote_jobs"]:
                        self.sync_remote_jobs(row)
                    else:
                        snapshot["warnings"].append("此服务器需升级至 OSS 1.3.0，才能提交关机后仍继续执行的模型任务。")
            except HTTPError as exc:
                if exc.code != 404:
                    snapshot["warnings"].append("状态接口：" + error_text(exc, row))
            except Exception as exc:
                snapshot["warnings"].append("状态接口：" + error_text(exc, row))
            try:
                ps = read_json(row, "/api/ps")
                snapshot["loaded"] = [x.get("name") or x.get("model") for x in ps.get("models", [])]
            except Exception as exc:
                snapshot["warnings"].append("已加载模型：" + error_text(exc, row))
        except Exception as exc:
            snapshot["error"] = error_text(exc, row)
        finally:
            snapshot["checked_at"] = now()
            with self.lock:
                # Do not apply an old probe after the user edits the endpoint.
                try:
                    current = self.store.get(row["id"])
                    if current == row:
                        self.snapshots[row["id"]] = snapshot
                except ApiError:
                    pass
                self.probing.discard(row["id"])

    def refresh(self, ident=None):
        with self.lock:
            for row in self.store.servers():
                if row["enabled"] and (ident is None or row["id"] == ident) and row["id"] not in self.probing:
                    self.probing.add(row["id"])
                    self.probes.submit(self.probe, row)

    def poll(self):
        while not self.stopping.is_set():
            self.refresh()
            self.stopping.wait(3)

    def overview(self):
        with self.lock:
            servers = []
            for row in self.store.servers():
                snapshot = dict(self.snapshots.get(row["id"], {"state": "checking", "online": False, "models": [], "error": ""}))
                if not row["enabled"]:
                    snapshot = {"state": "disabled", "online": False, "models": [], "error": ""}
                servers.append({**self.public_server(row), **snapshot, "refreshing": row["id"] in self.probing})
            return {"servers": servers, "jobs": self.store.jobs()[:200], "import_available": True,
                    "notice": "", "updated": now()}

    def create_job(self, ident, payload, kind):
        model = payload.get("model", "")
        if not isinstance(model, str) or not model.strip() or len(model) > 240 or any(c.isspace() or ord(c) < 32 for c in model.strip()):
            raise ApiError("请输入有效的模型名称，例如 qwen3:8b。")
        model = model.strip()
        with self.lock:
            if self.stopping.is_set():
                raise ApiError("管理器正在停止，不再接受新的模型任务。", 503)
            row = self.store.get(ident)
            if not row["enabled"]:
                raise ApiError("请先启用服务器。", 409)
            active = [j for j in self.store.jobs() if j["state"] in ACTIVE]
            if len(active) >= 16:
                raise ApiError("已有 16 个任务在进行，请等待任务完成。", 429)
            if any(j["server_id"] == ident and j["model"] == model for j in active):
                raise ApiError("该服务器上的这个模型已有进行中的任务。", 409)
            if kind == "delete" and payload.get("confirm_model") != model:
                raise ApiError("请输入完整模型名称以确认删除。")
            try:
                capability = read_json(row, "/oss/model-jobs")
            except HTTPError as exc:
                if exc.code == 404:
                    exc.close()
                    raise ApiError("请先将该服务器升级到 OSS 1.3.0；旧版接口无法保证本机关机后继续下载。", 426) from None
                raise
            if capability.get("schema") != 1 or capability.get("execution") != "server_owned":
                raise ApiError("服务器不支持远端后台任务，请先升级 OSS。", 426)
            job = {"id": uuid.uuid4().hex, "server_id": ident, "server_name": row["name"], "model": model,
                   "kind": kind, "state": "queued", "phase": "等待连接", "percent": None,
                   "completed": 0, "total": 0, "speed": 0, "error": "", "created": now(), "updated": now(),
                   "execution": "remote", "remote_id": ""}
            job["request_key"] = job["id"]
            self.store.save_job(job)
            return self.submit_remote(row, job)

    def merge_remote(self, row, remote, local=None):
        if (not isinstance(remote, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(remote.get("id", "")))
            or remote.get("state") not in ACTIVE | {"success", "failed", "unknown", "paused"}
            or remote.get("kind") not in {"pull", "delete"}):
            raise ApiError("远端任务响应无效。", 502)
        job = dict(local or {})
        if local and local.get("remote_id") == remote["id"] and str(local.get("updated", "")) > str(remote.get("updated", "")):
            return local  # A probe started before a control response must not undo its state.
        job.update({k: remote[k] for k in ("model", "kind", "state", "phase", "percent", "completed", "total", "speed", "error", "created", "updated", "request_key") if k in remote})
        job.update(id=job.get("id") or row["id"] + "-" + remote["id"], remote_id=remote["id"],
                   server_id=row["id"], server_name=row["name"], execution="remote")
        if job["state"] == "unknown":
            job["state"] = "interrupted"
        job["error"] = redact(job.get("error", ""), row)
        job["phase"] = redact(job.get("phase", ""), row)
        self.store.save_job(job)
        return job

    def control_job(self, ident, action):
        with self.lock:
            if self.stopping.is_set():
                raise ApiError("管理器正在停止。", 503)
            job = next((j for j in self.store.jobs() if j["id"] == ident), None)
            if not job:
                raise ApiError("任务不存在。", 404)
            if job["kind"] != "pull":
                raise ApiError("仅下载任务支持暂停和继续。", 409)
            if job.get("execution") != "remote":
                raise ApiError("这是旧版连接式任务，无法远程暂停。请在服务器升级 OSS 1.3.1 后重新提交下载，Ollama 会利用已下载文件。", 426)
            if not job.get("remote_id"):
                raise ApiError("远端尚未确认任务编号，请等待同步后重试。", 409)
            row = self.store.get(job["server_id"])
            if not row["enabled"]:
                raise ApiError("请先启用服务器。", 409)
            try:
                capability = read_json(row, "/oss/model-jobs")
                if action not in capability.get("controls", []):
                    raise ApiError("请将该服务器升级到 OSS 1.3.1，以支持暂停和继续下载。", 426)
                with upstream(row, "/oss/model-jobs/" + job["remote_id"] + "/" + action, "POST", {}, timeout=8) as response:
                    return self.merge_remote(row, json.loads(response.read(65536)), job)
            except ApiError:
                raise
            except Exception as exc:
                # An uncertain response is never presented as a successful pause/resume.
                detail = error_text(exc, row)
                code = exc.code if isinstance(exc, HTTPError) and exc.code in {404, 409, 429} else 502
                raise ApiError("控制结果未确认，请等待同步后核对任务状态：" + detail, code) from None

    def submit_remote(self, row, job):
        payload = {"kind": job["kind"], "model": job["model"], "request_key": job["request_key"]}
        if job["kind"] == "delete":
            payload["confirm_model"] = job["model"]
        try:
            with upstream(row, "/oss/model-jobs", "POST", payload, timeout=8) as response:
                return self.merge_remote(row, json.loads(response.read(65536)), job)
        except Exception as exc:
            definitive = isinstance(exc, HTTPError) and 400 <= exc.code < 500
            job.update(state="failed" if definitive else "queued", phase="提交被拒绝" if definitive else "提交结果待确认",
                       error=error_text(exc, row), updated=now())
            self.store.save_job(job)
            raise ApiError("远端未确认接收任务：" + job["error"] + ("" if definitive else " 将按原任务编号核对，请勿重复提交。"), 502) from None

    def sync_remote_jobs(self, row):
        remote = read_json(row, "/oss/model-jobs")
        if remote.get("schema") != 1 or not isinstance(remote.get("jobs"), list):
            raise ApiError("远端任务列表无效。", 502)
        with self.lock:
            if self.stopping.is_set():
                return
            local = [j for j in self.store.jobs() if j.get("server_id") == row["id"] and j.get("execution") == "remote"]
            by_key = {j.get("request_key"): j for j in local}
            seen = set()
            for item in remote["jobs"]:
                self.merge_remote(row, item, by_key.get(item.get("request_key")))
                seen.add(item.get("request_key"))
            for job in local:
                if job["state"] not in ACTIVE | {"paused"} or job.get("request_key") in seen:
                    continue
                if job.get("remote_id"):
                    self.merge_remote(row, read_json(row, "/oss/model-jobs/" + job["remote_id"]), job)
                else:
                    # An uncertain acceptance is retried only with its original durable key.
                    try:
                        self.submit_remote(row, job)
                    except ApiError:
                        pass

    def close(self):
        self.stopping.set()
        if self.poller:
            self.poller.join(timeout=2)
        self.probes.shutdown(wait=True, cancel_futures=True)
        self.store.db.close()

