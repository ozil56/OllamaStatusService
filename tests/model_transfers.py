"""Real HTTP transfer regression tests; no services, models or network downloads."""
import concurrent.futures
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ModelTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ollama-transfer-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.backend = f'http://127.0.0.1:{free_port()}'
        cls.direct = f'http://127.0.0.1:{free_port()}'
        cls.proxy = f'http://127.0.0.1:{free_port()}'
        cls.client = httpx.Client(timeout=5, trust_env=False)
        cls.addClassCleanup(cls.client.close)
        cls.start([str(ROOT/'tests'/'fake_backend.py'), '--port', cls.backend.rsplit(':', 1)[1]], 'backend')
        cls.wait_for(lambda: cls.client.get(cls.backend+'/api/version').status_code == 200)
        config = dict(backend_url=cls.backend, listen_host='127.0.0.1', listen_ipv6=False,
                      listen_port=int(cls.direct.rsplit(':', 1)[1]),
                      proxy_listen_port=int(cls.proxy.rsplit(':', 1)[1]),
                      data_dir=str(cls.work/'data'), queue_timeout_seconds=.2,
                      upstream_read_timeout_seconds=.3, request_deadline_seconds=.6,
                      health_interval_seconds=.1, shutdown_grace_seconds=.3)
        path = cls.work/'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        cls.start([str(ROOT/'app'/'run.py'), '--config', str(path)], 'gateway')
        cls.wait_for(lambda: cls.client.get(cls.direct+'/healthz').status_code == 200)

    @classmethod
    def start(cls, args, name):
        log = open(cls.work/(name+'.log'), 'wb')
        cls.addClassCleanup(log.close)
        process = subprocess.Popen([sys.executable]+args, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        cls.addClassCleanup(stop)

    @staticmethod
    def wait_for(predicate, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except httpx.HTTPError:
                pass
            time.sleep(.02)
        raise AssertionError('Condition did not become true')

    def status(self):
        return self.client.get(self.direct+'/status').json()

    def release(self, model):
        self.client.post(self.backend+'/__release/'+model)

    def pull(self, model, base=None, **kwargs):
        return self.client.post((base or self.direct)+'/api/pull', json={'model': model}, **kwargs)

    def test_01_two_pulls_reach_backend_together(self):
        models = ['held-parallel-a', 'held-parallel-b']
        with concurrent.futures.ThreadPoolExecutor() as pool:
            futures = []
            try:
                futures.append(pool.submit(self.pull, models[0]))
                self.wait_for(lambda: models[0] in self.client.get(self.backend+'/__transfers').json()['active'])
                futures.append(pool.submit(self.pull, models[1], self.proxy))
                self.wait_for(lambda: set(models) <= set(self.client.get(self.backend+'/__transfers').json()['active']), timeout=1)
                status = self.status()
                self.assertEqual(status['inflight_model_transfers'], 2)
                self.assertEqual(status['queued_requests'], 0)
                self.assertEqual(status['inflight_requests'], 0)
                self.assertTrue(status['can_start_now'])
                self.assertEqual(status['state'], 'busy')  # Maintenance must wait.
                self.assertEqual({t['origin'] for t in status['model_transfers']}, {'local', 'remote'})
            finally:
                for model in models:
                    self.release(model)
            for future in futures:
                response = future.result()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(json.loads(response.text.splitlines()[-1])['status'], 'success')
        self.wait_for(lambda: self.status()['state'] == 'idle')

    def test_02_stream_progress_and_long_quiet_download(self):
        model = 'held-long-transfer'
        try:
            with self.client.stream('POST', self.direct+'/api/pull', json={'model': model}) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['x-test-transfer'], 'preserved')
                lines = response.iter_lines()
                self.assertEqual(json.loads(next(lines))['status'], 'pulling manifest')
                # Longer than both the configured inference read and total deadlines.
                time.sleep(.85)
                self.assertEqual(self.status()['inflight_model_transfers'], 1)
                self.release(model)
                self.assertEqual(json.loads(next(lines))['status'], 'success')
        finally:
            self.release(model)
        self.wait_for(lambda: self.status()['state'] == 'idle')

    def test_03_transfer_does_not_block_inference_or_validate_queue_header(self):
        model = 'held-during-inference'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.pull, model, headers={'X-Ollama-Queue-Timeout': 'not-a-queue'})
            try:
                self.wait_for(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
                response = self.client.post(self.direct+'/api/generate', json={'model': 'fast', 'stream': False},
                                            headers={'X-Ollama-Queue-Timeout': '0'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['done'])
            finally:
                self.release(model)
            self.assertEqual(future.result().status_code, 200)

    def test_04_transfer_starts_while_inference_is_running(self):
        model = 'held-inference-transfer'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, self.direct+'/api/generate', json={'model': model, 'stream': False})
            try:
                self.wait_for(lambda: self.status()['inflight_requests'] == 1)
                response = self.pull('fast', headers={'X-Ollama-Queue-Timeout': '0'})
                self.assertEqual(response.status_code, 200)
            finally:
                self.release(model)
            self.assertEqual(future.result().status_code, 200)
        self.wait_for(lambda: self.status()['state'] == 'idle')

    def test_05_disconnect_cleans_up_without_unknown(self):
        model = 'held-cancel-transfer'
        try:
            with self.client.stream('POST', self.direct+'/api/pull', json={'model': model}) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn('pulling manifest', next(response.iter_lines()))
            self.wait_for(lambda: self.status()['inflight_model_transfers'] == 0)
            self.wait_for(lambda: model not in self.client.get(self.backend+'/__transfers').json()['active'])
            self.assertEqual(self.status()['uncertain_requests'], 0)
            self.assertEqual(self.status()['state'], 'idle')
        finally:
            self.release(model)

    def test_06_nonstream_and_backend_error_remain_compatible(self):
        for base in (self.direct, self.proxy):
            for path in ('/api/pull', '/api/push'):
                with self.subTest(base=base, path=path):
                    response = self.client.post(base+path, json={'model': 'fast', 'stream': False})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'status': 'success'})
                    response = self.client.post(base+path, json={'model': 'reject-transfer'})
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {'error': 'model not found'})
        self.wait_for(lambda: self.status()['state'] == 'idle')

    def test_07_push_and_pull_share_no_queue(self):
        model = 'held-push-transfer'
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.client.post, self.direct+'/api/push', json={'model': model})
            try:
                self.wait_for(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
                self.assertEqual(self.pull('fast').status_code, 200)
            finally:
                self.release(model)
            self.assertEqual(future.result().status_code, 200)

    def test_08_disconnect_before_nonstream_response_headers(self):
        model = 'held-cancel-before-headers'
        payload = json.dumps({'model': model, 'stream': False}).encode()
        port = int(self.direct.rsplit(':', 1)[1])
        try:
            with socket.create_connection(('127.0.0.1', port)) as sock:
                sock.sendall(f'POST /api/pull HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n'.encode()+payload)
                self.wait_for(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
            self.wait_for(lambda: self.status()['inflight_model_transfers'] == 0)
            self.assertEqual(self.status()['state'], 'idle')
        finally:
            self.release(model)


if __name__ == '__main__':
    unittest.main(verbosity=2)
