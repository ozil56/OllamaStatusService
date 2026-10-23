"""Real HTTP update-admission tests with isolated ports, models and ProgramData."""
import concurrent.futures
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class UpdateGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='oss-update-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.work = Path(cls.temp.name)
        cls.marker = cls.work / 'OllamaStatusService' / 'updater' / 'maintenance.lock'
        cls.marker.parent.mkdir(parents=True)
        cls.relay_port = port()
        cls.network_config = cls.work / 'download-network.json'
        cls.network_config.write_text(json.dumps({'port': cls.relay_port, 'caller_sid': 'S-1-0', 'preserved_proxies': []}))
        cls.backend = f'http://127.0.0.1:{port()}'
        cls.direct = f'http://127.0.0.1:{port()}'
        cls.proxy = f'http://127.0.0.1:{port()}'
        cls.client = httpx.Client(timeout=15, trust_env=False)
        cls.addClassCleanup(cls.client.close)
        cls.pool = concurrent.futures.ThreadPoolExecutor(4)
        cls.addClassCleanup(cls.pool.shutdown)
        cls.start([str(ROOT/'tests'/'fake_backend.py'), '--port', cls.backend.rsplit(':', 1)[1]], 'backend')
        cls.wait(lambda: cls.client.get(cls.backend+'/api/version').status_code == 200)
        config = dict(backend_url=cls.backend, listen_host='127.0.0.1', listen_ipv6=False,
                      listen_port=int(cls.direct.rsplit(':', 1)[1]), proxy_listen_port=int(cls.proxy.rsplit(':', 1)[1]),
                      data_dir=str(cls.work/'data'), health_interval_seconds=.1,
                      request_deadline_seconds=30, upstream_read_timeout_seconds=30)
        file = cls.work/'config.json'
        file.write_text(json.dumps(config), encoding='utf-8')
        cls.start([str(ROOT/'app'/'run.py'), '--config', str(file)], 'gateway')
        cls.wait(lambda: cls.status()['ollama_online'])

    @classmethod
    def start(cls, args, name):
        log = open(cls.work/(name+'.log'), 'wb')
        cls.addClassCleanup(log.close)
        env = dict(os.environ, PROGRAMDATA=str(cls.work), OSS_DOWNLOAD_NETWORK=str(cls.network_config))
        process = subprocess.Popen([sys.executable]+args, stdout=log, stderr=log, env=env,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        cls.addClassCleanup(stop)

    @classmethod
    def wait(cls, function, seconds=10):
        deadline = time.monotonic()+seconds
        error = None
        while time.monotonic() < deadline:
            try:
                value = function()
                if value:
                    return value
            except (httpx.HTTPError, KeyError) as exc:
                error = exc
            time.sleep(.04)
        raise AssertionError(f'timed out: {error}')

    @classmethod
    def status(cls):
        return cls.client.get(cls.direct+'/status').json()

    def setUp(self):
        self.marker.unlink(missing_ok=True)
        self.wait(lambda: self.status()['state'] == 'idle')

    def tearDown(self):
        self.marker.unlink(missing_ok=True)

    def test_gate_rejects_both_ingresses_and_all_mutation_paths(self):
        self.marker.write_text('{}')
        state = self.status()
        self.assertTrue(state['ollama_update']['safe_to_stop'])
        self.assertFalse(state['can_start_now'])
        self.assertFalse(state['accepting_requests'])
        for base in (self.direct, self.proxy):
            for method, path in [('POST','/api/generate'),('POST','/api/pull'),('POST','/api/push'),
                                 ('POST','/api/blobs/sha256:abc'),('DELETE','/api/delete'),
                                 ('POST','/oss/model-jobs'),('POST','/oss/model-jobs/missing/resume'),
                                 ('POST','/manage/api/jobs')]:
                response = self.client.request(method, base+path, json={'model':'simulated:1b'})
                self.assertEqual(response.status_code, 503, (base,path,response.text))
                self.assertIs(response.json()['forwarded'], False)
                self.assertEqual(response.headers['x-ollama-forwarded'], 'false')
            for path in ('/status','/healthz','/api/version','/api/tags','/oss/model-jobs'):
                self.assertEqual(self.client.get(base+path).status_code, 200)
        self.marker.unlink()
        response = self.client.post(self.direct+'/api/generate', json={'model':'ok','stream':False})
        self.assertEqual(response.status_code, 200)

    def test_running_and_queued_inference_are_not_cancelled(self):
        model = 'held-update-inference'
        first = self.pool.submit(self.client.post, self.direct+'/api/generate', json={'model':model,'stream':False})
        self.wait(lambda: self.status()['inflight_requests'] == 1)
        second = self.pool.submit(self.client.post, self.proxy+'/api/generate', json={'model':'ok','stream':False})
        self.wait(lambda: self.status()['queued_requests'] == 1)
        try:
            self.marker.write_text('{}')
            self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
            self.assertFalse(first.done())
        finally:
            self.client.post(self.backend+'/__release/'+model)
        self.assertEqual(first.result(10).status_code, 200)
        self.assertEqual(second.result(10).status_code, 200)
        self.wait(lambda: self.status()['ollama_update']['safe_to_stop'])

    def test_download_continues_while_gate_is_closed(self):
        model = 'held-update-download'
        transfer = self.pool.submit(self.client.post, self.direct+'/api/pull', json={'model':model,'stream':False})
        self.wait(lambda: self.status()['inflight_model_transfers'] == 1)
        try:
            self.marker.write_text('{}')
            self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
            self.assertFalse(transfer.done())
        finally:
            self.client.post(self.backend+'/__release/'+model)
        self.assertEqual(transfer.result(10).status_code, 200)
        self.wait(lambda: self.status()['ollama_update']['safe_to_stop'])

    def test_server_owned_download_and_paused_job(self):
        model = 'held-update-owned'
        created = self.client.post(self.direct+'/oss/model-jobs', json={'kind':'pull','model':model,'request_key':'update-owned-request-0001'})
        self.assertEqual(created.status_code, 202, created.text)
        ident = created.json()['id']
        self.wait(lambda: self.status()['model_jobs']['active'] == 1)
        self.marker.write_text('{}')
        self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
        self.marker.unlink()
        self.assertEqual(self.client.post(self.direct+'/oss/model-jobs/'+ident+'/pause').status_code, 200)
        self.wait(lambda: self.status()['model_jobs']['active'] == 0)
        self.marker.write_text('{}')
        self.wait(lambda: self.status()['ollama_update']['safe_to_stop'])
        self.assertEqual(self.client.get(self.direct+'/oss/model-jobs/'+ident).json()['state'], 'paused')
        self.assertEqual(self.client.post(self.direct+'/oss/model-jobs/'+ident+'/resume').status_code, 503)

    def test_body_still_uploading_prevents_stop(self):
        host, number = self.direct.removeprefix('http://').split(':')
        with socket.create_connection((host,int(number)), timeout=5) as sock:
            sock.sendall(b'POST /api/blobs/sha256:abc HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\nConnection: close\r\n\r\n12')
            self.wait(lambda: self.status()['ollama_update']['inflight_http_operations'] > 0)
            self.marker.write_text('{}')
            self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
            sock.sendall(b'34567890')
            while sock.recv(65536):
                pass
        self.wait(lambda: self.status()['ollama_update']['safe_to_stop'])

    def test_relay_pending_connection_prevents_stop(self):
        self.assertTrue(self.status()['download_network']['ready'])
        with socket.create_connection(('127.0.0.1', self.relay_port), timeout=5) as sock:
            sock.sendall(b'GET / HTTP/1.1\r\n')
            self.wait(lambda: self.status()['download_network']['active_tunnels'] == 1)
            self.marker.write_text('{}')
            self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
            sock.sendall(b'\r\n')
            self.assertIn(b'502', sock.recv(4096))
        self.wait(lambda: self.status()['ollama_update']['safe_to_stop'])

    def test_unknown_model_job_blocks_upgrade(self):
        import sqlite3
        with closing(sqlite3.connect(self.work/'data'/'model-jobs.sqlite3')) as db, db:
            job = dict(id='unknown-update',request_key='unknown-update',state='unknown',kind='delete',model='x')
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'],job['request_key'],json.dumps(job)))
        try:
            self.marker.write_text('{}')
            self.assertEqual(self.status()['ollama_update']['unknown_model_jobs'], 1)
            self.assertFalse(self.status()['ollama_update']['safe_to_stop'])
        finally:
            with closing(sqlite3.connect(self.work/'data'/'model-jobs.sqlite3')) as db, db:
                db.execute("DELETE FROM jobs WHERE id='unknown-update'")


if __name__ == '__main__':
    unittest.main()
