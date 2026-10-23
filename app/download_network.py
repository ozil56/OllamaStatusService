"""Loopback CONNECT relay for Ollama. TLS stays between Ollama and its origin.

No certificate interception, insecure pulls, system proxy changes or service
restarts. New tunnels discover configured proxies; existing tunnels keep their
route. Only known model hosts may use the IPv4 Fake-IP benchmarking range.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import unquote, urlsplit

LOG = logging.getLogger("ollama_status.download_network")
MIN_OLLAMA = (0, 40, 9)
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
MODEL_DOMAINS = ("ollama.com", "ollama.ai", "hf.co", "huggingface.co", "r2.cloudflarestorage.com")
MAX_HEADER = 16384


class NetworkError(Exception):
    """Public, credential-free diagnostics only."""


class ProxyDenied(NetworkError):
    pass


def model_host(host):
    return any(host == domain or host.endswith("." + domain) for domain in MODEL_DOMAINS)


def authority(value):
    # CONNECT only accepts an ASCII DNS name or literal IP with explicit :443.
    if not isinstance(value, str) or not value.isascii() or any(c.isspace() for c in value):
        raise NetworkError("Invalid CONNECT authority")
    match = re.fullmatch(r"(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+):443", value)
    if not match:
        raise NetworkError("Only HTTPS CONNECT on port 443 is supported")
    host = match[1].strip("[]").lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or "." not in host or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")):
            raise NetworkError("Invalid destination hostname") from None
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
            raise NetworkError("Local destinations are not allowed")
    else:
        if not address.is_global or address.is_multicast:
            raise NetworkError("Non-public IP destinations are not allowed")
    return host, 443


def approved_address(host, value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if address.is_global and not address.is_multicast:
        return True
    return isinstance(address, ipaddress.IPv4Address) and address in FAKE_IP and model_host(host)


@dataclass(frozen=True, repr=False)
class Proxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    def __repr__(self):
        return "<configured proxy>"  # URLs may contain secrets.


def parse_proxy(value, own_port):
    if not isinstance(value, str) or not value or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    if "://" not in value:
        value = "http://" + value
    try:
        url = urlsplit(value)
        if url.scheme not in {"http", "https", "socks5", "socks5h"} or not url.hostname or url.path not in {"", "/"} or url.query or url.fragment:
            return None
        port = url.port or (443 if url.scheme == "https" else 1080 if url.scheme.startswith("socks") else 80)
        if not 1 <= port <= 65535:
            return None
        host = url.hostname.lower().rstrip(".")
        if host in {"localhost", "127.0.0.1", "::1"} and port == own_port:
            return None
        username, password = unquote(url.username or ""), unquote(url.password or "")
        if any(ord(c) < 32 or ord(c) == 127 for c in username + password):
            return None
        return Proxy(url.scheme, host, port, username, password)
    except (ValueError, UnicodeError):
        return None


def windows_proxy_values(sid):
    """Read the original user's settings, even though OSS runs as LocalService."""
    if os.name != "nt":
        return []
    import winreg
    values = []
    roots = [(winreg.HKEY_USERS, sid + r"\Environment"),
             (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                for name in ("HTTPS_PROXY", "ALL_PROXY"):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                        if isinstance(value, str):
                            values.append(value)
                    except OSError:
                        pass
        except OSError:
            pass
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, sid + r"\Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            if winreg.QueryValueEx(key, "ProxyEnable")[0]:
                value = winreg.QueryValueEx(key, "ProxyServer")[0]
                if isinstance(value, str):
                    if "=" in value:
                        parts = dict(p.strip().split("=", 1) for p in value.split(";") if "=" in p)
                        value = parts.get("https", parts.get("http", ""))
                    values.append(value)
    except (OSError, ValueError):
        pass
    return values


class ProxyDiscovery:
    def __init__(self, config, own_port):
        self.config, self.own_port = config, own_port
        self.cached, self.expires = [], 0

    def candidates(self):
        if time.monotonic() >= self.expires:
            values = windows_proxy_values(self.config.get("caller_sid", ""))
            values += self.config.get("preserved_proxies", [])
            values += [os.environ.get(k, "") for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")]
            self.cached = list(dict.fromkeys(p for value in values if (p := parse_proxy(value, self.own_port))))[:4]
            self.expires = time.monotonic() + 5
        return self.cached


class DownloadNetwork:
    def __init__(self, config, discovery=None):
        self.config = config
        port = config.get("port")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid download relay port")
        if not isinstance(config.get("preserved_proxies", []), list) or not re.fullmatch(r"S-1-[0-9-]+", config.get("caller_sid", "S-1-0")):
            raise ValueError("Invalid download relay configuration")
        self.discovery = discovery or ProxyDiscovery(config, port)
        self.server = None
        self.tasks = set()
        self.unavailable = {}
        self.last_route, self.last_error = "ready", ""
        self.backend_version = ""

    @classmethod
    def from_environment(cls):
        path = os.environ.get("OSS_DOWNLOAD_NETWORK")
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig"))) if path else None

    def status(self):
        parsed = re.match(r"^(\d+)\.(\d+)\.(\d+)", self.backend_version)
        return {"enabled": True, "ready": self.server is not None, "route": self.last_route,
                "active_tunnels": len(self.tasks), "last_error": self.last_error,
                "minimum_ollama_version": ".".join(map(str, MIN_OLLAMA)),
                "requires_ollama_update": bool(parsed and tuple(map(int, parsed.groups())) < MIN_OLLAMA)}

    async def start(self):
        # Explicit AF_INET loopback; no LAN exposure or firewall opening.
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", self.config["port"], family=socket.AF_INET, limit=MAX_HEADER + 1)
        self.port = self.server.sockets[0].getsockname()[1]
        if isinstance(self.discovery, ProxyDiscovery):
            self.discovery.own_port = self.port

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def read_header(self, reader):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        if len(raw) > MAX_HEADER:
            raise NetworkError("CONNECT header too large")
        return raw

    async def connect_proxy(self, proxy, host):
        tls = ssl.create_default_context() if proxy.scheme == "https" else None
        reader, writer = await asyncio.wait_for(asyncio.open_connection(proxy.host, proxy.port, ssl=tls,
            server_hostname=proxy.host if tls else None, limit=MAX_HEADER + 1), 3)
        try:
            peer = writer.get_extra_info("peername")
            if peer and ipaddress.ip_address(peer[0]).is_loopback and peer[1] == self.port:
                raise OSError("Proxy points back to this relay")
            if proxy.scheme.startswith("socks"):
                methods = b"\x00\x02" if proxy.username else b"\x00"
                writer.write(b"\x05" + bytes([len(methods)]) + methods)
                await writer.drain()
                version, method = await asyncio.wait_for(reader.readexactly(2), 5)
                if version != 5 or method not in (0, 2) or (method == 2 and not proxy.username):
                    raise ProxyDenied("Configured SOCKS proxy requires unsupported authentication")
                if method == 2:
                    user, password = proxy.username.encode(), proxy.password.encode()
                    if len(user) > 255 or len(password) > 255:
                        raise ProxyDenied("Configured SOCKS credentials are too long")
                    writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
                    await writer.drain()
                    if await asyncio.wait_for(reader.readexactly(2), 5) != b"\x01\x00":
                        raise ProxyDenied("Configured proxy rejected authentication")
                domain = host.encode("ascii")
                writer.write(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + b"\x01\xbb")
                await writer.drain()
                reply = await asyncio.wait_for(reader.readexactly(4), 8)
                if reply[:2] != b"\x05\x00":
                    raise ProxyDenied("Configured proxy refused the destination")
                size = {1: 4, 4: 16}.get(reply[3])
                if reply[3] == 3:
                    size = (await asyncio.wait_for(reader.readexactly(1), 5))[0]
                if size is None:
                    raise ProxyDenied("Invalid SOCKS proxy response")
                await asyncio.wait_for(reader.readexactly(size + 2), 5)
            else:
                target = "[" + host + "]:443" if ":" in host else host + ":443"
                auth = ""
                if proxy.username:
                    credentials = base64.b64encode((proxy.username + ":" + proxy.password).encode()).decode("ascii")
                    auth = "Proxy-Authorization: Basic " + credentials + "\r\n"
                writer.write(("CONNECT " + target + " HTTP/1.1\r\nHost: " + target + "\r\n" + auth + "\r\n").encode("ascii"))
                await writer.drain()
                reply = await self.read_header(reader)
                if not re.match(rb"HTTP/1\.[01] 200(?: |\r\n)", reply):
                    # Never bypass an explicit authentication/access denial.
                    raise ProxyDenied("Configured proxy refused HTTPS CONNECT")
            return reader, writer
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise

    async def direct_connect(self, host):
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 8)
        if not addresses or any(not approved_address(host, row[4][0]) for row in addresses):
            raise NetworkError("Destination resolves to a disallowed address")
        # Dial the checked addresses, never resolve the name a second time.
        # A Fake-IP for an approved model domain is handled by the existing TUN.
        for family, _, _, _, address in addresses:
            try:
                return await asyncio.wait_for(asyncio.open_connection(address[0], 443, family=family), 8)
            except (OSError, asyncio.TimeoutError):
                continue
        raise NetworkError("Download destination is unreachable through the current network")

    async def connect(self, host):
        for proxy in self.discovery.candidates():
            if self.unavailable.get(proxy, 0) > time.monotonic():
                continue
            try:
                result = await self.connect_proxy(proxy, host)
                self.last_route = "detected_proxy"
                return result
            except (ProxyDenied, ssl.SSLCertVerificationError):
                raise  # Do not silently bypass access rules or certificate failures.
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                self.unavailable[proxy] = time.monotonic() + 30
        result = await self.direct_connect(host)
        self.last_route = "local_forwarder"
        return result

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        if len(self.tasks) >= 128:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
            writer.close()
            return
        self.tasks.add(task)
        upstream = None
        pumps = []
        established = False
        try:
            raw = await self.read_header(reader)
            first = raw.split(b"\r\n", 1)[0].decode("ascii")
            method, target, version = first.split(" ")
            if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise NetworkError("Only HTTPS CONNECT is supported")
            host, _ = authority(target)
            remote, upstream = await self.connect(host)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            established = True
            self.last_error = ""

            async def pump(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
                if destination.can_write_eof():
                    destination.write_eof()

            pumps = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(remote, writer))]
            done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            # Closing either side ends the CONNECT session, including cancelled
            # downloads. No retry after any TLS/application data was exchanged.
            for completed in done:
                completed.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, NetworkError) else "Download connection failed; check proxy, network and certificates"
            LOG.warning("Download connection: %s", self.last_error)
            if not established:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                try:
                    await writer.drain()
                except (OSError, ConnectionError):
                    pass
        finally:
            for pump_task in pumps:
                pump_task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (upstream, writer):
                if stream:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except (OSError, ConnectionError):
                        pass
            self.tasks.discard(task)
