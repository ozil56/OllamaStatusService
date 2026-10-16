"""Real TCP/HTTP acceptance tests on temporary ports, with a controlled backend.
No installed service, FRP, firewall, model or GPU is touched.
"""
import concurrent.futures
from dataclasses import asdict
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from gateway import Settings, VERSION
from run import bind_listener, configured_listeners, gateway_listeners

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with bind_listener('0.0.0.0', 0, socket.AF_INET) as listener:
        return listener.getsockname()[1]


class ConfigurationTests(unittest.TestCase):
    def test_explicit_ipv6_scope_is_preserved_when_binding(self):
        listener = Mock()
        address = (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('fe80::123', 19136, 0, 4))
        with patch('run.socket.getaddrinfo', return_value=[address]), patch('run.bind_listener', return_value=listener) as bind:
            with configured_listeners('fe80::123%4', 19136, True):
                bind.assert_called_once_with('fe80::123%4', 19136, socket.AF_INET6)
        listener.close.assert_called_once_with()

    def test_old_config_gets_loopback_proxy_defaults(self):
        settings = Settings(**{'listen_host': '0.0.0.0', 'queue_timeout_seconds': 71})
        settings.validate()
        self.assertEqual(settings.proxy_listen_host, '127.0.0.1')
        self.assertEqual(settings.proxy_listen_port, 11436)
        self.assertEqual(settings.queue_timeout_seconds, 71)

    def test_invalid_or_colliding_proxy_config_is_rejected(self):
        for value in (11434, 11435, 0, 65536, True, '11436', 11436.5, None):
            with self.subTest(port=value), self.assertRaises(ValueError):
                Settings(proxy_listen_port=value).validate()
        for value in ('', '  ', None, 123):
            with self.subTest(host=value), self.assertRaises(ValueError):
                Settings(proxy_listen_host=value).validate()

    def test_proxy_port_conflict_releases_all_direct_sockets(self):
        direct_port = free_port()
        with bind_listener('127.0.0.1', 0, socket.AF_INET) as occupied:
            proxy_port = occupied.getsockname()[1]
            settings = Settings(listen_port=direct_port, proxy_listen_port=proxy_port)
            with self.assertRaises(OSError):
                with gateway_listeners(settings):
                    self.fail('Must fail when proxy listener is occupied')
            for host, family in (('0.0.0.0', socket.AF_INET), ('::', socket.AF_INET6)):
                with bind_listener(host, direct_port, family):
                    pass

    def test_default_proxy_binding_is_loopback_and_cleanup_is_complete(self):
        settings = Settings(listen_port=free_port(), proxy_listen_port=free_port())
        with gateway_listeners(settings) as listeners:
            proxies = [s for s in listeners if s.getsockname()[1] == settings.proxy_listen_port]
            self.assertEqual([s.getsockname()[0] for s in proxies], ['127.0.0.1'])
        self.assertTrue(all(s.fileno() == -1 for s in listeners))


class ProxyHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ollama-proxy-ingress-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.backend_port = free_port()
        cls.backend = f'http://127.0.0.1:{cls.backend_port}'
        cls.spawn([str(ROOT/'tests'/'fake_backend.py'), '--port', str(cls.backend_port)], 'backend')
        cls.wait_url(cls.backend + '/api/version')
        cls.settings = Settings(backend_url=cls.backend, listen_port=free_port(),
                                proxy_listen_port=free_port(), data_dir=str(cls.work/'data'),
                                queue_timeout_seconds=15, max_queue_size=2,
                                health_interval_seconds=.1, shutdown_grace_seconds=.3)
        cls.direct = f'http://127.0.0.1:{cls.settings.listen_port}'
        cls.proxy = f'http://127.0.0.1:{cls.settings.proxy_listen_port}'
        cls.start_gateway(cls.settings, 'gateway')

    @classmethod
    def spawn(cls, args, name):
        log = open(cls.work/(name+'.log'), 'wb')
        cls.addClassCleanup(log.close)
        process = subprocess.Popen([sys.executable] + args, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        cls.addClassCleanup(stop)
        return process

    @classmethod
    def start_gateway(cls, settings, name):
        path = cls.work/(name+'.json')
        path.write_text(json.dumps(asdict(settings)), encoding='utf-8-sig')
        cls.spawn([str(ROOT/'app'/'run.py'), '--config', str(path)], name)
        cls.wait_url(f'http://127.0.0.1:{settings.listen_port}/healthz')
        cls.wait_url(f'http://127.0.0.1:{settings.proxy_listen_port}/healthz')

    @classmethod
    def wait_url(cls, url):
        for _ in range(100):
            try:
                response = httpx.get(url, trust_env=False, timeout=.3)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(.05)
        logs = '\n'.join(p.read_text(errors='replace') for p in cls.work.glob('*.log'))
        raise AssertionError(f'Cannot start {url}\n{logs}')

    def wait_status(self, predicate, base=None):
        for _ in range(200):
            value = httpx.get((base or self.direct)+'/status', trust_env=False, timeout=1).json()
            if predicate(value):
                return value
            time.sleep(.01)
        self.fail(f'Status did not converge: {value}')

    def setUp(self):
        self.wait_status(lambda s: s['state'] == 'idle')

    def post(self, base, model, headers=None, stream=False):
        return httpx.post(base+'/api/generate', json={'model': model, 'stream': stream},
                          headers=headers, timeout=20, trust_env=False)

    def received(self):
        return httpx.get(self.backend+'/__received', trust_env=False).json()['models']

    def release(self, model):
        httpx.post(self.backend+'/__release/'+model, trust_env=False).raise_for_status()

    def test_probe_origins_and_host_forwarding_header_spoofing(self):
        spoof = {'Host': f'127.0.0.1:{self.settings.listen_port}', 'X-Forwarded-For': '127.0.0.1',
                 'Forwarded': 'for=127.0.0.1', 'X-Real-IP': '::1', 'X-Ollama-Priority': 'local',
                 'X-Ollama-Origin': 'local', 'Origin': 'http://localhost'}
        for base, origin in ((self.direct, 'local'), (self.proxy, 'remote')):
            for path in ('/help', '/healthz', '/.well-known/ollama-status-service'):
                response = httpx.get(base+path, headers=spoof, trust_env=False)
                self.assertEqual(response.json()['request_origin'], origin)
                self.assertEqual(response.json()['version'], VERSION)
            help_value = httpx.get(base+'/help', trust_env=False).json()['queue_policy']['priority']
            self.assertEqual(help_value['proxy_listener']['port'], self.settings.proxy_listen_port)
            self.assertTrue(help_value['proxy_listener']['shared_queue'])

    def test_remote_then_local_queue_runs_local_first_without_interrupting(self):
        before = len(self.received())
        held = 'held-priority'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            active = pool.submit(self.post, self.proxy, held)
            try:
                status = self.wait_status(lambda s: s['inflight_requests'] == 1)
                self.assertEqual(status['active_origin'], 'remote')
                remote = pool.submit(self.post, self.proxy, 'remote-waiting', {'Host': '127.0.0.1:11434', 'X-Ollama-Priority': 'local'})
                self.wait_status(lambda s: s['queued_remote_requests'] == 1)
                local = pool.submit(self.post, self.direct, 'local-waiting')
                status = self.wait_status(lambda s: s['queued_requests'] == 2)
                self.assertEqual((status['queued_local_requests'], status['queued_remote_requests']), (1, 1))
                proxy_status = httpx.get(self.proxy+'/status', trust_env=False).json()
                self.assertEqual({r['id'] for r in status['requests']}, {r['id'] for r in proxy_status['requests']})
                self.assertEqual(self.received()[before:], [held])
                self.assertFalse(active.done())
            finally:
                self.release(held)
            for future in (active, remote, local):
                self.assertEqual(future.result(timeout=5).status_code, 200)
        self.assertEqual(self.received()[before:], [held, 'local-waiting', 'remote-waiting'])

    def test_queue_capacity_and_zero_wait_are_shared_across_ports(self):
        held = 'held-capacity'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            active = pool.submit(self.post, self.direct, held)
            try:
                self.wait_status(lambda s: s['active_origin'] == 'local')
                for base in (self.proxy, self.direct):
                    response = self.post(base, 'zero', {'X-Ollama-Queue-Timeout': '0'})
                    self.assertEqual(response.status_code, 429)
                    self.assertEqual(response.json()['error'], 'busy_no_queue')
                first = pool.submit(self.post, self.proxy, 'capacity-remote')
                self.wait_status(lambda s: s['queued_requests'] == 1)
                second = pool.submit(self.post, self.direct, 'capacity-local')
                self.wait_status(lambda s: s['queued_requests'] == 2)
                for base in (self.proxy, self.direct):
                    response = self.post(base, 'overflow')
                    self.assertEqual(response.status_code, 429)
                    self.assertEqual(response.json()['error'], 'queue_full')
                    self.assertFalse(response.json()['forwarded'])
            finally:
                self.release(held)
            for future in (active, first, second):
                self.assertEqual(future.result(timeout=5).status_code, 200)

    def test_streaming_on_both_ports_and_proxy_inference_origin(self):
        for base, origin in ((self.proxy, 'remote'), (self.direct, 'local')):
            with httpx.stream('POST', base+'/api/generate', json={'model':'slow', 'stream':True},
                              timeout=10, trust_env=False) as response:
                self.assertEqual(response.status_code, 200)
                lines = response.iter_lines()
                self.assertFalse(json.loads(next(lines))['done'])
                self.assertEqual(self.wait_status(lambda s: s['inflight_requests'] == 1)['active_origin'], origin)
                self.assertTrue(json.loads(list(lines)[-1])['done'])
            self.wait_status(lambda s: s['state'] == 'idle')

    def test_configured_dual_stack_proxy_always_remote_and_shared_state(self):
        settings = Settings(backend_url=self.backend, listen_port=free_port(), proxy_listen_port=free_port(),
                            proxy_listen_host='0.0.0.0', data_dir=str(self.work/'dual-proxy'),
                            health_interval_seconds=.1, shutdown_grace_seconds=.3)
        self.start_gateway(settings, 'dual-proxy')
        direct = f'http://[::1]:{settings.listen_port}'
        proxy4 = f'http://127.0.0.1:{settings.proxy_listen_port}'
        proxy6 = f'http://[::1]:{settings.proxy_listen_port}'
        for base in (proxy4, proxy6):
            self.assertEqual(httpx.get(base+'/healthz', trust_env=False).json()['request_origin'], 'remote')
        self.assertEqual(httpx.get(direct+'/healthz', trust_env=False).json()['request_origin'], 'local')
        self.wait_status(lambda s: s['state'] == 'idle', direct)
        held = 'held-ipv6'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            active = pool.submit(self.post, proxy6, held)
            try:
                status = self.wait_status(lambda s: s['inflight_requests'] == 1, direct)
                self.assertEqual(status['active_origin'], 'remote')
                self.assertEqual(httpx.get(proxy4+'/status', trust_env=False).json()['active_origin'], 'remote')
            finally:
                self.release(held)
            self.assertEqual(active.result(timeout=5).status_code, 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
