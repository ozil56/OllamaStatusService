"""Durable, server-owned model jobs. HTTP clients only submit and observe."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

import httpx

ACTIVE = {"queued", "running"}


def utc():
    return datetime.now(timezone.utc).isoformat()


class JobError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ModelJobs:
    def __init__(self, directory, client, delete_model, transfers):
        self.client, self.delete_model, self.transfers = client, delete_model, transfers
        self.db = sqlite3.connect(str(Path(directory) / "model-jobs.sqlite3"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")
        self.db.commit()
        self.tasks = {}
        self.control_lock = asyncio.Lock()
        self.closing = False

    def jobs(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM jobs ORDER BY rowid DESC")]

    def get(self, ident):
        row = self.db.execute("SELECT payload FROM jobs WHERE id=?", (ident,)).fetchone()
        if not row:
            raise JobError("Task not found", 404)
        return json.loads(row[0])

    def save(self, job):
        job["updated"] = utc()
        self.db.execute("INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                        (job["id"], job["request_key"], json.dumps(job, ensure_ascii=False)))
        self.db.commit()

    def start(self):
        for job in reversed(self.jobs()):
            if job["state"] not in ACTIVE:
                continue
            if job["kind"] == "pull":
                job.update(state="queued", phase="等待恢复下载", speed=0)
                self.save(job)
                self.launch(job)
            else:
                # Never replay a possibly executed destructive operation.
                job.update(state="unknown", phase="请核对模型列表", error="OSS 重启，删除结果尚未确认。", speed=0)
                self.save(job)

    def submit(self, payload, origin):
        model, kind, key = payload.get("model"), payload.get("kind"), payload.get("request_key")
        if not isinstance(model, str) or not model or model != model.strip() or len(model) > 240 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
            raise JobError("Invalid model name")
        if kind not in {"pull", "delete"}:
            raise JobError("kind must be pull or delete")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise JobError("A stable request_key of 16-128 characters is required")
        if kind == "delete" and payload.get("confirm_model") != model:
            raise JobError("confirm_model must match model")
        existing = self.db.execute("SELECT payload FROM jobs WHERE request_key=?", (key,)).fetchone()
        if existing:
            job = json.loads(existing[0])
            if job["model"] != model or job["kind"] != kind:
                raise JobError("request_key was already used for another operation", 409)
            return job
        if self.closing:
            raise JobError("OSS is stopping", 503)
        active = [j for j in self.jobs() if j["state"] in ACTIVE]
        if any(j["model"] == model and j["state"] in ACTIVE | {"paused"} for j in self.jobs()):
            raise JobError("This model already has an active or paused task", 409)
        if len(active) >= 16:
            raise JobError("Too many active model tasks", 429)
        job = dict(id=uuid.uuid4().hex, request_key=key, model=model, kind=kind, origin=origin,
                   state="queued", phase="等待远端 OSS 执行", percent=None, completed=0, total=0,
                   speed=0, error="", created=utc(), updated=utc(), execution="remote")
        self.save(job)  # Commit before returning an acceptance or forwarding to Ollama.
        self.launch(job)
        return job

    def launch(self, job):
        task = asyncio.create_task(self.run(job), name="model-job-" + job["id"])
        self.tasks[job["id"]] = task  # Strong reference; unrelated to the submitting request.
        def finished(done):
            if self.tasks.get(job["id"]) is done:
                self.tasks.pop(job["id"], None)
        task.add_done_callback(finished)

    async def control(self, ident, action):
        # Serialize cancellation and relaunch so repeated clicks cannot create two streams.
        async with self.control_lock:
            if self.closing:
                raise JobError("OSS is stopping", 503)
            job = self.get(ident)
            if action not in {"pause", "resume"}:
                raise JobError("Unknown task action", 404)
            if job["kind"] != "pull":
                raise JobError("Only pull tasks can be paused or resumed", 409)
            if action == "pause":
                if job["state"] == "paused":
                    return job
                if job["state"] not in ACTIVE:
                    raise JobError("This task is no longer downloading", 409)
                # Persist intent first: even a crash while closing the stream must stay paused.
                job.update(state="paused", phase="已暂停，已下载文件保留在服务器", speed=0, error="")
                self.save(job)
                task = self.tasks.get(ident)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                return self.get(ident)
            if job["state"] in ACTIVE:
                return job
            if job["state"] not in {"paused", "failed"}:
                raise JobError("Only paused or failed pulls can be resumed", 409)
            active = [j for j in self.jobs() if j["state"] in ACTIVE]
            if any(j["model"] == job["model"] and j["id"] != ident and j["state"] in ACTIVE | {"paused"} for j in self.jobs()):
                raise JobError("This model already has another active or paused task", 409)
            if len(active) >= 16:
                raise JobError("Too many active model tasks", 429)
            job.update(state="queued", phase="重新连接，等待 Ollama 确认续传进度", speed=0, error="")
            self.save(job)
            self.launch(job)
            return job

    async def run(self, job):
        try:
            job.update(state="running", phase="连接 Ollama", error="", speed=0)
            self.save(job)
            if job["kind"] == "pull":
                self.transfers[job["id"]] = dict(id=job["id"], model=job["model"], endpoint="/api/pull",
                    origin=job["origin"], started_at=utc(), execution="remote")
                await self.pull(job)
            else:
                job["phase"] = "等待服务器执行删除"
                self.save(job)
                response = await self.delete_model(job["model"], job["origin"])
                response.raise_for_status()
                if response.content and response.json().get("error"):
                    raise JobError(str(response.json()["error"]))
            job.update(state="success", phase="下载完成" if job["kind"] == "pull" else "已删除模型", percent=100, speed=0)
        except asyncio.CancelledError:
            if self.get(job["id"])["state"] == "paused":
                job.update(state="paused", phase="已暂停，已下载文件保留在服务器", speed=0, error="")
            else:
                job.update(state="queued" if job["kind"] == "pull" else "unknown", speed=0,
                           phase="等待 OSS 恢复后继续" if job["kind"] == "pull" else "删除结果待核对")
            raise
        except Exception as exc:
            # Do not expose arbitrary upstream bodies, paths or headers.
            detail = str(exc)[:400] if isinstance(exc, JobError) else (
                "Ollama 返回 HTTP " + str(exc.response.status_code) if isinstance(exc, httpx.HTTPStatusError)
                else "无法完成操作，请检查 Ollama 连接及模型名称。")
            job.update(state="failed", phase="操作未完成", error=detail, speed=0)
        finally:
            self.transfers.pop(job["id"], None)
            self.save(job)

    async def pull(self, job):
        success, layers = False, {}
        previous, last_save = None, time.monotonic()
        timeout = httpx.Timeout(connect=10, read=None, write=30, pool=10)
        async with self.client.stream("POST", "/api/pull", json={"model": job["model"], "stream": True}, timeout=timeout) as response:
            response.raise_for_status()
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, buffer = buffer.partition(b"\n")
                    if not raw.strip():
                        continue
                    if len(raw) > 1024 * 1024:
                        raise JobError("下载进度响应过大")
                    event = json.loads(raw)
                    if event.get("error"):
                        raise JobError(str(event["error"]))
                    phase = str(event.get("status", "下载中"))
                    success = success or phase == "success"
                    if event.get("digest") and event.get("total"):
                        total = max(0, int(event["total"]))
                        done = min(total, max(0, int(event.get("completed", 0))))
                        layers[str(event["digest"])] = (done, total)
                    completed = sum(v[0] for v in layers.values())
                    total = sum(v[1] for v in layers.values())
                    moment = time.monotonic()
                    if moment - last_save >= .5 or phase != job["phase"] or success:
                        speed = max(0, round((completed - previous) / max(.001, moment - last_save))) if previous is not None else 0
                        job.update(phase=phase, speed=speed)
                        # Manifest events carry no byte counts. Keep the last known progress
                        # until Ollama supplies a fresh count, without counting cached bytes as speed.
                        if total:
                            job.update(completed=completed, total=total, percent=round(completed / total * 100, 1))
                        self.save(job)
                        last_save, previous = moment, completed if total else None
                if len(buffer) > 1024 * 1024:
                    raise JobError("下载进度响应过大")
            if buffer.strip():
                event = json.loads(buffer)
                if event.get("error"):
                    raise JobError(str(event["error"]))
                success = success or event.get("status") == "success"
        if not success:
            raise JobError("连接已结束，但没有收到 Ollama 的完成确认。")

    async def close(self):
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.db.close()
