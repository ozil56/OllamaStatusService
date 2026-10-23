"""Administrative, filesystem-owned admission gate for an Ollama update.

Only the elevated updater writes the marker in ProgramData (outside data/).
There is deliberately no HTTP endpoint that can enable/disable maintenance.
The counter includes requests still uploading their body and unscheduled model
operations. Existing work continues; the updater must observe safe_to_stop.
"""
import os
from pathlib import Path

from starlette.responses import JSONResponse


class UpdateGuard:
    def __init__(self, marker=None):
        self.marker = Path(marker) if marker else Path(os.environ.get(
            "PROGRAMDATA", r"C:\ProgramData")) / "OllamaStatusService" / "updater" / "maintenance.lock"
        self.inflight = 0

    @property
    def active(self):
        try:
            return self.marker.stat() is not None
        except FileNotFoundError:
            return False
        except OSError:
            return True  # Permission/I/O errors must not reopen admission.

    @staticmethod
    def read_only(scope):
        path = scope.get("path", "").rstrip("/")
        return scope.get("method") in {"GET", "HEAD", "OPTIONS"} and (
            path in {"", "/status", "/healthz", "/help", "/dashboard", "/openapi.json",
                     "/.well-known/ollama-status-service", "/api/version", "/api/tags", "/api/ps",
                     "/v1/models", "/oss/model-jobs", "/manage"}
            or path.startswith(("/oss/model-jobs/", "/manage/")))


class UpdateAdmission:
    def __init__(self, app, guard):
        self.app, self.guard = app, guard

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.guard.read_only(scope):
            return await self.app(scope, receive, send)
        # No await between checking the marker and registering the request.
        # A marker created concurrently is covered by this inflight counter.
        if self.guard.active:
            response = JSONResponse({"error": "Ollama update maintenance; retry later", "forwarded": False},
                status_code=503, headers={"X-Ollama-Forwarded": "false", "Retry-After": "30"})
            return await response(scope, receive, send)
        self.guard.inflight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.guard.inflight -= 1
