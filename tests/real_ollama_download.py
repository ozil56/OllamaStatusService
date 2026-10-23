"""Opt-in Internet smoke test; isolated Ollama, gateway, ports and model folder.

Requires --ollama pointing to the official executable. Downloads all-minilm:22m
through /manage (about 46 MB), without inference or production service changes.
No DNS, proxy, certificate-store or TUN settings are modified.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ollama", required=True)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    executable = Path(args.ollama).resolve(strict=True)
    sid = list(csv.reader(subprocess.check_output(["whoami.exe", "/user", "/fo", "csv", "/nh"],
        text=True, creationflags=subprocess.CREATE_NO_WINDOW).splitlines()))[0][1]
    with tempfile.TemporaryDirectory(prefix="oss-real-download-") as directory:
        work = Path(directory)
        backend, direct, proxy, relay = (port() for _ in range(4))
        if len({backend, direct, proxy, relay}) != 4:
            raise RuntimeError("Test port collision; rerun")
        network = work / "download-network.json"
        network.write_text(json.dumps(dict(port=relay, caller_sid=sid, preserved_proxies=[])))
        config = work / "config.json"
        config.write_text(json.dumps(dict(backend_url=f"http://127.0.0.1:{backend}", listen_host="127.0.0.1",
            listen_ipv6=False, listen_port=direct, proxy_listen_port=proxy, data_dir=str(work / "data"),
            health_interval_seconds=.2, shutdown_grace_seconds=2)))
        backend_env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{backend}", OLLAMA_MODELS=str(work / "models"),
            HTTPS_PROXY=f"http://127.0.0.1:{relay}", HTTP_PROXY="", ALL_PROXY="", NO_PROXY="localhost,127.0.0.1,::1")
        gateway_env = dict(os.environ, PROGRAMDATA=str(work), OSS_DOWNLOAD_NETWORK=str(network))
        processes, logs = [], []
        try:
            for command, env, label in (([str(executable), "serve"], backend_env, "ollama"),
                    ([sys.executable, str(ROOT / "app" / "run.py"), "--config", str(config)], gateway_env, "gateway")):
                log = (work / (label + ".log")).open("wb")
                logs.append(log)
                processes.append(subprocess.Popen(command, env=env, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW))
            with httpx.Client(base_url=f"http://127.0.0.1:{direct}", trust_env=False, timeout=15) as client:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if client.get("/status").json()["ollama_online"]:
                            break
                    except (httpx.HTTPError, KeyError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Isolated services failed to start")
                    time.sleep(.2)
                version = client.get("/api/version").json()["version"]
                print("Isolated official Ollama:", version, flush=True)
                headers = {"X-OSS-Token": client.get("/manage/api/session").json()["token"]}
                response = client.post("/manage/api/servers/00000000000000000000000000000001/pull",
                    json={"model": "all-minilm:22m"}, headers=headers)
                response.raise_for_status()
                ident = response.json()["id"]
                deadline, next_report = time.monotonic() + args.timeout, 0
                while True:
                    client.post("/manage/api/refresh", json={}, headers=headers).raise_for_status()
                    job = next(j for j in client.get("/manage/api/overview").json()["jobs"] if j["id"] == ident)
                    status = client.get("/status").json()["download_network"]
                    if time.monotonic() >= next_report:
                        print("Download state:", job["state"], "route:", status["route"], flush=True)
                        next_report = time.monotonic() + 10
                    if job["state"] == "success":
                        break
                    if job["state"] in {"failed", "unknown"} or time.monotonic() >= deadline:
                        raise RuntimeError("Isolated download failed: " + json.dumps(job, ensure_ascii=False))
                    time.sleep(.5)
                inventory = client.get("/api/tags").json()["models"]
                assert any(model["name"] == "all-minilm:22m" for model in inventory), inventory
                assert status["route"] in {"detected_proxy", "local_forwarder"}, status
                print("PASS /manage -> official Ollama -> OSS relay -> real HTTPS model download and inventory; no inference", flush=True)
        finally:
            # Only these test-owned child processes and TemporaryDirectory are removed.
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            for log in logs:
                log.close()


if __name__ == "__main__":
    main()
