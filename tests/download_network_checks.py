"""Real TCP/CONNECT/TLS tests. No system proxy, DNS or service changes.

Fake-IP resolution and dialing are injected only inside this test process.
An ephemeral certificate is trusted only by the test client's SSLContext.
"""
import asyncio
import ipaddress
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from download_network import DownloadNetwork, NetworkError, Proxy, ProxyDiscovery, approved_address, authority, parse_proxy


class PolicyTests(unittest.TestCase):
    def test_fake_ip_is_limited_to_model_domains(self):
        self.assertTrue(approved_address("registry.ollama.ai", "198.18.0.204"))
        self.assertTrue(approved_address("test.r2.cloudflarestorage.com", "198.19.255.254"))
        for host in ("evil.example", "ollama.ai.evil.example", "notollama.ai"):
            self.assertFalse(approved_address(host, "198.18.0.204"))
        for address in ("127.0.0.1", "192.168.1.1", "169.254.169.254", "100.100.2.148", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1"):
            self.assertFalse(approved_address("registry.ollama.ai", address), address)

    def test_connect_authority_blocks_non_https_and_private_literals(self):
        self.assertEqual(authority("Registry.Ollama.AI.:443"), ("registry.ollama.ai", 443))
        for value in ("127.0.0.1:443", "198.18.0.204:443", "[::1]:443", "localhost:443", "metadata.internal:443",
                      "registry.ollama.ai:80", "registry.ollama.ai:443/path", "user@registry.ollama.ai:443", "registry.ollama.ai:443\r\nInjected: yes"):
            with self.assertRaises(NetworkError, msg=value):
                authority(value)

    def test_proxy_parsing_and_secrets(self):
        p = parse_proxy("http://user:p%40ss@127.0.0.1:7890", 11437)
        self.assertEqual(p.password, "p@ss")
        self.assertNotIn("p@ss", repr(p))
        self.assertEqual(parse_proxy("127.0.0.1:7890", 11437).scheme, "http")
        self.assertEqual(parse_proxy("socks5://localhost:1080", 11437).port, 1080)
        for value in ("http://127.0.0.1:11437", "http://localhost:11437", "ftp://proxy:21", "http://proxy:9/path", "http://proxy:99999", "http://u%0d:p@proxy:8"):
            self.assertIsNone(parse_proxy(value, 11437), value)

    def test_discovery_prefers_user_settings_deduplicates_and_skips_self(self):
        with patch("download_network.windows_proxy_values", return_value=["127.0.0.1:7890", "127.0.0.1:7890"]), \
             patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:11437", "ALL_PROXY": "socks5://localhost:1080"}, clear=True):
            d = ProxyDiscovery({"caller_sid": "S-1-5-1", "preserved_proxies": ["127.0.0.1:7891"]}, 11437)
            self.assertEqual([p.port for p in d.candidates()], [7890, 7891, 1080])
            d.expires = 0
            with patch("download_network.windows_proxy_values", return_value=["127.0.0.1:7892"]):
                self.assertEqual(d.candidates()[0].port, 7892)

    def test_old_ollama_is_reported_without_restarting_anything(self):
        relay = DownloadNetwork({"port": 0})
        relay.backend_version = "0.40.2"
        self.assertTrue(relay.status()["requires_ollama_update"])
        relay.backend_version = "0.40.9"
        self.assertFalse(relay.status()["requires_ollama_update"])


class Candidates:
    def __init__(self):
        self.values = []
    def candidates(self):
        return list(self.values)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="oss-relay-tls-")
        cls.cert = Path(cls.directory.name) / "cert.pem"
        cls.key = Path(cls.directory.name) / "key.pem"
        openssl = shutil.which("openssl") or next((p for p in (
            r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\msys64\mingw64\bin\openssl.exe") if Path(p).is_file()), None)
        if not openssl:
            raise RuntimeError("OpenSSL is required to generate an isolated test certificate")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(cls.key),
            "-out", str(cls.cert), "-subj", "/CN=registry.ollama.ai", "-addext", "subjectAltName=DNS:registry.ollama.ai"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    async def asyncSetUp(self):
        self.connections = set()
        self.release = asyncio.Event()
        self.proxies = []
        self.hits = []
        self.sni = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        context.set_servername_callback(lambda sock, name, ctx: self.sni.append(name))
        self.origin = await asyncio.start_server(self.origin_handler, "127.0.0.1", 0, ssl=context)
        self.origin_port = self.origin.sockets[0].getsockname()[1]
        self.discovery = Candidates()
        self.relay = DownloadNetwork({"port": 0}, self.discovery)
        await self.relay.start()
        self.original_open = asyncio.open_connection
        self.original_lookup = asyncio.get_running_loop().getaddrinfo
        self.dialed = []

        async def lookup(host, port, **kwargs):
            if host in {"registry.ollama.ai", "evil.example"}:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.204", port))]
            return await self.original_lookup(host, port, **kwargs)

        async def open_connection(host=None, port=None, **kwargs):
            if host == "198.18.0.204":
                self.dialed.append((host, port))
                return await self.original_open("127.0.0.1", self.origin_port, **kwargs)
            return await self.original_open(host, port, **kwargs)

        self.lookup_patch = patch.object(asyncio.get_running_loop(), "getaddrinfo", side_effect=lookup)
        self.open_patch = patch("download_network.asyncio.open_connection", side_effect=open_connection)
        self.lookup_patch.start()
        self.open_patch.start()

    async def asyncTearDown(self):
        self.release.set()
        await self.relay.close()
        for server in self.proxies + [self.origin]:
            server.close()
            await server.wait_closed()
        for task in list(self.connections):
            task.cancel()
        await asyncio.gather(*list(self.connections), return_exceptions=True)
        self.open_patch.stop()
        self.lookup_patch.stop()

    def client(self, trusted=True):
        context = ssl.create_default_context(cafile=str(self.cert)) if trusted else ssl.create_default_context()
        return httpx.AsyncClient(proxy=f"http://127.0.0.1:{self.relay.port}", verify=context, trust_env=False, timeout=10)

    async def origin_handler(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            if b"GET /hold " in request:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\nfirst")
                await writer.drain()
                await self.release.wait()
                writer.write(b"!")
            else:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.connections.discard(task)

    async def proxy(self, label="http", deny=False, socks=False):
        async def handler(reader, writer):
            task = asyncio.current_task()
            self.connections.add(task)
            remote_writer = None
            pumps = []
            try:
                if socks:
                    self.assertEqual(await reader.readexactly(3), b"\x05\x01\x00")
                    writer.write(b"\x05\x00"); await writer.drain()
                    self.assertEqual(await reader.readexactly(4), b"\x05\x01\x00\x03")
                    size = (await reader.readexactly(1))[0]
                    name = await reader.readexactly(size)
                    self.assertEqual(name, b"registry.ollama.ai")
                    self.assertEqual(await reader.readexactly(2), b"\x01\xbb")
                    self.hits.append(label)
                    writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x01\xbb")
                else:
                    request = await reader.readuntil(b"\r\n\r\n")
                    self.assertTrue(request.startswith(b"CONNECT registry.ollama.ai:443 HTTP/1.1\r\n"))
                    self.hits.append(label)
                    if deny:
                        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                        await writer.drain()
                        return
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                remote, remote_writer = await self.original_open("127.0.0.1", self.origin_port)
                async def pump(a, b):
                    while data := await a.read(65536):
                        b.write(data); await b.drain()
                pumps = [asyncio.create_task(pump(reader, remote_writer)), asyncio.create_task(pump(remote, writer))]
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for p in pumps:
                    p.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                for w in (remote_writer, writer):
                    if w:
                        w.close()
                        try:
                            await w.wait_closed()
                        except OSError:
                            pass
                self.connections.discard(task)
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.proxies.append(server)
        return Proxy("socks5" if socks else "http", "127.0.0.1", server.sockets[0].getsockname()[1])

    async def test_builtin_fake_ip_path_preserves_tls_hostname_and_certificate(self):
        async with self.client() as client:
            response = await client.get("https://registry.ollama.ai/v2/")
        self.assertEqual(response.text, "OK")
        self.assertEqual(self.relay.last_route, "local_forwarder")
        self.assertEqual(self.dialed, [("198.18.0.204", 443)])
        self.assertIn("registry.ollama.ai", self.sni)
        self.assertEqual(self.relay.server.sockets[0].getsockname()[0], "127.0.0.1")

    async def test_invalid_certificate_is_still_rejected(self):
        async with self.client(False) as client:
            with self.assertRaises(httpx.ConnectError):
                await client.get("https://registry.ollama.ai/v2/")

    async def test_existing_http_proxy_is_selected_without_local_target_dns(self):
        self.discovery.values = [await self.proxy()]
        async with self.client() as client:
            self.assertEqual((await client.get("https://registry.ollama.ai/v2/")).text, "OK")
        self.assertEqual(self.hits, ["http"])
        self.assertEqual(self.dialed, [])
        self.assertEqual(self.relay.last_route, "detected_proxy")

    async def test_socks_proxy_receives_domain_for_remote_resolution(self):
        self.discovery.values = [await self.proxy("socks", socks=True)]
        async with self.client() as client:
            self.assertEqual((await client.get("https://registry.ollama.ai/v2/")).text, "OK")
        self.assertEqual(self.hits, ["socks"])
        self.assertEqual(self.dialed, [])

    async def test_unavailable_proxy_falls_back_but_denied_proxy_does_not(self):
        with socket.socket() as temporary:
            temporary.bind(("127.0.0.1", 0))
            port = temporary.getsockname()[1]
        self.discovery.values = [Proxy("http", "127.0.0.1", port)]
        async with self.client() as client:
            self.assertEqual((await client.get("https://registry.ollama.ai/v2/")).text, "OK")
        self.assertEqual(self.relay.last_route, "local_forwarder")
        self.dialed.clear()
        self.discovery.values = [await self.proxy(deny=True)]
        async with self.client() as client:
            with self.assertRaises(httpx.ProxyError):
                await client.get("https://registry.ollama.ai/v2/")
        self.assertEqual(self.dialed, [])
        self.assertIn("refused", self.relay.last_error)

    async def test_proxy_change_does_not_interrupt_existing_download(self):
        first, second = await self.proxy("first"), await self.proxy("second")
        self.discovery.values = [first]
        async with self.client() as client:
            async with client.stream("GET", "https://registry.ollama.ai/hold") as response:
                chunks = response.aiter_bytes()
                self.assertEqual(await anext(chunks), b"first")
                self.discovery.values = [second]
                async with self.client() as other:
                    self.assertEqual((await other.get("https://registry.ollama.ai/v2/")).text, "OK")
                self.release.set()
                self.assertEqual(b"".join([chunk async for chunk in chunks]), b"!")
        self.assertEqual(self.hits, ["first", "second"])

    async def test_self_proxy_connection_falls_back_without_recursion(self):
        self.discovery.values = [Proxy("http", "127.0.0.1", self.relay.port)]
        async with self.client() as client:
            self.assertEqual((await client.get("https://registry.ollama.ai/v2/")).text, "OK")
        self.assertEqual(self.relay.last_route, "local_forwarder")
        self.assertEqual(self.dialed, [("198.18.0.204", 443)])

    async def test_mixed_public_and_private_dns_is_rejected_before_dial(self):
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ("1.1.1.1", "127.0.0.1")]
        with patch.object(asyncio.get_running_loop(), "getaddrinfo", return_value=addresses):
            with self.assertRaises(NetworkError):
                await self.relay.direct_connect("registry.ollama.ai")
        self.assertEqual(self.dialed, [])

    async def test_unknown_fake_domain_is_not_dialed(self):
        async with self.client() as client:
            with self.assertRaises(httpx.ProxyError):
                await client.get("https://evil.example/v2/")
        self.assertEqual(self.dialed, [])

    async def test_malformed_or_non_https_connect_never_reaches_network(self):
        for request in (b"GET http://registry.ollama.ai/ HTTP/1.1\r\n\r\n", b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n",
                        b"CONNECT registry.ollama.ai:80 HTTP/1.1\r\n\r\n"):
            reader, writer = await self.original_open("127.0.0.1", self.relay.port)
            writer.write(request); await writer.drain()
            self.assertIn(b"502", await reader.read())
            writer.close(); await writer.wait_closed()
        self.assertEqual(self.dialed, [])


if __name__ == "__main__":
    unittest.main()
