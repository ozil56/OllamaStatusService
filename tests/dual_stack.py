"""Native IPv4/IPv6 sockets and real HTTP against an isolated fake backend.

Uses temporary ports/data; never changes installed services or firewall rules.
"""
import concurrent.futures
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from gateway import Settings, VERSION
from run import bind_listener, dual_stack_listeners

ROOT = Path(__file__).resolve().parents[1]


class ListenerTests(unittest.TestCase):
    def test_same_port_both_families_and_cleanup(self):
        with dual_stack_listeners(0) as listeners:
            self.assertEqual({s.family for s in listeners}, {socket.AF_INET, socket.AF_INET6})
            self.assertEqual(len({s.getsockname()[1] for s in listeners}), 1)
            self.assertEqual(listeners[1].getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 1)
            port = listeners[0].getsockname()[1]
            for host in ('127.0.0.1', '::1'):
                with socket.create_connection((host, port), timeout=2):
                    pass
        self.assertTrue(all(s.fileno() == -1 for s in listeners))

    def test_disabled_ipv6_falls_back_to_ipv4_with_warning(self):
        def without_ipv6(host, port, family):
            if family == socket.AF_INET6:
                raise OSError(errno.EAFNOSUPPORT, 'IPv6 disabled in fixture')
            return bind_listener(host, port, family)
        with patch('run.bind_listener', side_effect=without_ipv6):
            with self.assertLogs('ollama_status.listener', level='WARNING') as logs:
                with dual_stack_listeners(0) as listeners:
                    self.assertEqual(len(listeners), 1)
                    with socket.create_connection(('127.0.0.1', listeners[0].getsockname()[1]), timeout=2):
                        pass
            self.assertIn('IPv6 is unavailable', logs.output[0])

    def test_ipv6_conflict_fails_and_releases_ipv4(self):
        with bind_listener('::', 0, socket.AF_INET6) as occupied:
            port = occupied.getsockname()[1]
            with self.assertRaises(OSError):
                with dual_stack_listeners(port):
                    self.fail('Must not silently fall back when IPv6 is occupied')
            with bind_listener('0.0.0.0', port, socket.AF_INET):
                pass

    def test_invalid_ipv6_config_is_rejected(self):
        self.assertTrue(Settings().listen_ipv6)
        for value in ('false', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings(listen_ipv6=value).validate()


class NativeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ollama-dual-stack-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            cls.backend_port = probe.getsockname()[1]
        cls.backend = f'http://127.0.0.1:{cls.backend_port}'
        cls.spawn([str(ROOT/'tests'/'fake_backend.py'), '--port', str(cls.backend_port)], 'backend')
        cls.wait_url(cls.backend + '/api/version')
        # Deliberately omit listen_ipv6: this is an unchanged 1.2.1 config.
        cls.v4, cls.v6 = cls.start_gateway('legacy')

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
    def start_gateway(cls, name, **overrides):
        with dual_stack_listeners(0) as listeners:
            port = listeners[0].getsockname()[1]
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            proxy_port = probe.getsockname()[1]
        values = dict(proxy_listen_port=proxy_port, backend_url=cls.backend, listen_host='0.0.0.0', listen_port=port,
                      data_dir=str(cls.work/name), health_interval_seconds=.1,
                      queue_timeout_seconds=5, shutdown_grace_seconds=.3)
        values.update(overrides)
        config = cls.work/(name+'.json')
        config.write_text(json.dumps(values), encoding='utf-8-sig')
        before = config.read_bytes()
        cls.spawn([str(ROOT/'app'/'run.py'), '--config', str(config)], name)
        v4, v6 = f'http://127.0.0.1:{port}', f'http://[::1]:{port}'
        cls.wait_url((v6 if values['listen_host'] == '::1' else v4) + '/healthz')
        if config.read_bytes() != before:
            raise AssertionError('Starting the gateway changed the legacy config')
        return v4, v6

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
        raise AssertionError(f'Failed to start {url}\n{logs}')

    def wait_status(self, base, predicate):
        for _ in range(100):
            status = httpx.get(base+'/status', trust_env=False, timeout=1).json()
            if predicate(status):
                return status
            time.sleep(.02)
        self.fail(f'Status did not converge: {status}')

    def test_legacy_config_serves_both_families_and_same_version(self):
        for base in (self.v4, self.v6):
            with self.subTest(base=base):
                health = httpx.get(base+'/healthz', trust_env=False)
                self.assertEqual(health.json()['version'], VERSION)
                self.assertEqual(health.headers['x-ollama-status-service'], VERSION)
                self.assertEqual(httpx.get(base+'/api/tags', trust_env=False).status_code, 200)
                self.assertEqual(httpx.get(base+'/help', trust_env=False).json()['version'], VERSION)

    def test_streaming_generation_on_both_families(self):
        self.wait_status(self.v4, lambda s: s['state'] == 'idle')
        for base in (self.v4, self.v6):
            with httpx.stream('POST', base+'/api/generate', json={'model':'fast','stream':True},
                              trust_env=False, timeout=5) as response:
                self.assertEqual(response.status_code, 200)
                lines = [json.loads(line) for line in response.iter_lines() if line]
                self.assertFalse(lines[0]['done'])
                self.assertTrue(lines[-1]['done'])

    def test_ipv4_and_ipv6_share_one_queue_and_local_origin(self):
        self.wait_status(self.v4, lambda s: s['state'] == 'idle')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first = pool.submit(httpx.post, self.v6+'/api/generate',
                                json={'model':'slow','stream':False}, trust_env=False, timeout=8)
            busy = self.wait_status(self.v4, lambda s: s['inflight_requests'] == 1)
            self.assertEqual(busy['active_origin'], 'local')
            second = pool.submit(httpx.post, self.v4+'/api/generate',
                                 json={'model':'fast','stream':False}, trust_env=False, timeout=8)
            queued = self.wait_status(self.v6, lambda s: s['queued_requests'] == 1)
            self.assertEqual(queued['queued_local_requests'], 1)
            self.assertEqual(queued['queued_remote_requests'], 0)
            self.assertEqual(first.result().status_code, 200)
            self.assertEqual(second.result().status_code, 200)
        self.wait_status(self.v6, lambda s: s['state'] == 'idle')

    def test_opt_out_keeps_ipv4_only(self):
        v4, v6 = self.start_gateway('ipv4-only', listen_ipv6=False)
        self.assertEqual(httpx.get(v4+'/healthz', trust_env=False).status_code, 200)
        with self.assertRaises(httpx.ConnectError):
            httpx.get(v6+'/healthz', trust_env=False, timeout=3)

    def test_explicit_ipv4_loopback_is_not_widened(self):
        v4, v6 = self.start_gateway('loopback-v4', listen_host='127.0.0.1')
        self.assertEqual(httpx.get(v4+'/healthz', trust_env=False).status_code, 200)
        with self.assertRaises(httpx.ConnectError):
            httpx.get(v6+'/healthz', trust_env=False, timeout=3)

    def test_explicit_ipv6_loopback_is_supported(self):
        v4, v6 = self.start_gateway('loopback-v6', listen_host='::1')
        self.assertEqual(httpx.get(v6+'/healthz', trust_env=False).status_code, 200)
        with self.assertRaises(httpx.ConnectError):
            httpx.get(v4+'/healthz', trust_env=False, timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
