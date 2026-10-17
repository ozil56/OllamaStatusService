"""Real-HTTP tests for server-owned jobs; all models and downloads are simulated."""
import concurrent.futures
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from model_jobs import ModelJobs


class QueuedPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_before_worker_starts_and_restart_keep_intent(self):
        calls=[]
        async def fake(request):
            calls.append(request)
            return httpx.Response(200,content=b'{"status":"success"}\n')
        with tempfile.TemporaryDirectory() as folder:
            async with httpx.AsyncClient(base_url='http://fixture',transport=httpx.MockTransport(fake)) as client:
                jobs=ModelJobs(folder,client,None,{})
                job=jobs.submit(dict(kind='pull',model='queued:test',request_key=uuid.uuid4().hex),'local')
                self.assertEqual((await jobs.control(job['id'],'pause'))['state'],'paused')
                self.assertEqual(calls,[])
                await jobs.close()
                jobs=ModelJobs(folder,client,None,{})
                jobs.start()
                self.assertEqual(jobs.get(job['id'])['state'],'paused')
                await jobs.control(job['id'],'resume')
                await asyncio.gather(*list(jobs.tasks.values()))
                self.assertEqual(jobs.get(job['id'])['state'],'success')
                self.assertEqual(len(calls),1)
                await jobs.close()


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class OwnedJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.backend, self.direct, self.proxy = ['http://127.0.0.1:' + str(port()) for _ in range(3)]
        self.client = httpx.Client(trust_env=False, timeout=5)
        self.addCleanup(self.client.close)
        self.start([str(ROOT/'tests/fake_backend.py'), '--port', self.backend.rsplit(':', 1)[1]], 'backend')
        self.wait(lambda: self.client.get(self.backend+'/api/version').status_code == 200)
        config = dict(backend_url=self.backend, listen_host='127.0.0.1', listen_ipv6=False,
                      listen_port=int(self.direct.rsplit(':', 1)[1]), proxy_listen_port=int(self.proxy.rsplit(':', 1)[1]),
                      data_dir=str(self.work/'data'), queue_timeout_seconds=.2,
                      health_interval_seconds=.1, shutdown_grace_seconds=.2)
        self.config = self.work/'config.json'
        self.config.write_text(json.dumps(config), encoding='utf-8')
        self.gateway = self.start_gateway()

    def start(self, args, name):
        log = open(self.work/(name + '-' + uuid.uuid4().hex[:6] + '.log'), 'wb')
        self.addCleanup(log.close)
        process = subprocess.Popen([sys.executable]+args, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=8)
        self.addCleanup(stop)
        return process

    def start_gateway(self):
        process = self.start([str(ROOT/'app/run.py'), '--config', str(self.config)], 'gateway')
        self.wait(lambda: self.client.get(self.direct+'/healthz').status_code == 200)
        return process

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = predicate()
                if value: return value
            except (httpx.HTTPError, KeyError):
                pass
            time.sleep(.04)
        self.fail('Condition timed out')

    def submit(self, model, key=None, kind='pull', client=None):
        return (client or self.client).post(self.proxy+'/oss/model-jobs', json={
            'kind':kind, 'model':model, 'confirm_model':model, 'request_key':key or uuid.uuid4().hex})

    def get(self, job):
        return self.client.get(self.direct+'/oss/model-jobs/'+job['id']).json()

    def release(self, model):
        self.client.post(self.backend+'/__release/'+model)

    def control(self, job, action):
        return self.client.post(self.proxy+'/oss/model-jobs/'+job['id']+'/'+action)

    def test_pause_disconnects_and_resume_reuses_task_and_progress(self):
        model = 'held-progress-pause'
        job = self.submit(model).json()
        self.wait(lambda: self.get(job)['completed'] == 983)
        self.assertEqual(self.get(job)['speed'], 0)  # Cached/resumed bytes aren't network speed.
        paused = self.control(job, 'pause')
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()['state'], 'paused')
        self.assertEqual(paused.json()['completed'], 983)
        self.wait(lambda: model not in self.client.get(self.backend+'/__transfers').json()['active'])
        self.assertEqual(self.control(job, 'pause').json()['state'], 'paused')
        self.assertEqual(self.submit(model).status_code, 409)
        self.assertEqual(self.submit(model, kind='delete').status_code, 409)
        self.assertEqual(self.client.get(self.direct+'/status').json()['model_jobs']['active'], 0)
        self.assertEqual(self.control(job, 'resume').json()['id'], job['id'])
        self.wait(lambda: self.get(job)['state'] == 'running')
        self.release(model)
        self.wait(lambda: self.get(job)['state'] == 'success')
        self.assertEqual(self.control(job, 'pause').status_code, 409)
        self.assertEqual(self.control(job, 'resume').status_code, 409)

    def test_paused_task_stays_paused_after_restart(self):
        model = 'held-progress-paused-restart'
        job = self.submit(model).json()
        self.wait(lambda: self.get(job)['completed'] == 983)
        self.assertEqual(self.control(job, 'pause').status_code, 200)
        self.gateway.terminate()
        self.gateway.wait(timeout=8)
        self.gateway = self.start_gateway()
        self.assertEqual(self.get(job)['state'], 'paused')
        self.assertEqual(self.get(job)['completed'], 983)
        self.assertNotIn(model, self.client.get(self.backend+'/__transfers').json()['active'])
        self.assertEqual(self.control(job, 'resume').status_code, 200)
        self.wait(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
        self.release(model)
        self.wait(lambda: self.get(job)['state'] == 'success')

    def test_concurrent_controls_do_not_duplicate_streams(self):
        model = 'held-progress-concurrent'
        job = self.submit(model).json()
        self.wait(lambda: self.get(job)['completed'] == 983)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for action in ('pause', 'resume', 'pause', 'resume'):
                results = list(pool.map(lambda _: self.control(job, action), range(6)))
                self.assertTrue(all(r.status_code == 200 for r in results))
                if action == 'pause':
                    self.wait(lambda: model not in self.client.get(self.backend+'/__transfers').json()['active'])
                else:
                    self.wait(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
        self.assertEqual(self.client.get(self.backend+'/__transfers').json()['calls'][model], 3)
        self.release(model)
        self.wait(lambda: self.get(job)['state'] == 'success')

    def test_failed_pull_can_retry_but_delete_and_unknown_actions_rejected(self):
        job = self.submit('reject-transfer').json()
        self.wait(lambda: self.get(job)['state'] == 'failed')
        self.assertEqual(self.control(job, 'resume').status_code, 200)
        self.wait(lambda: self.get(job)['state'] == 'failed')
        deleted = self.submit('test-delete', kind='delete').json()
        self.assertEqual(self.control(deleted, 'pause').status_code, 409)
        self.assertEqual(self.control(deleted, 'resume').status_code, 409)
        self.assertEqual(self.control(job, 'nonsense').status_code, 404)
        self.assertEqual(self.control({'id':'missing'}, 'pause').status_code, 404)

    def test_client_disconnect_does_not_cancel_and_progress_is_observable(self):
        model = 'held-owned-disconnect'
        with httpx.Client(trust_env=False, timeout=3) as submitting_client:
            response = self.submit(model, client=submitting_client)
            self.assertEqual(response.status_code, 202)
            job = response.json()
        self.wait(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
        self.wait(lambda: self.get(job)['phase'] == 'pulling manifest')
        self.assertEqual(self.get(job)['state'], 'running')
        self.assertEqual(self.client.get(self.direct+'/status').json()['model_jobs']['active'], 1)
        self.release(model)
        self.wait(lambda: self.get(job)['state'] == 'success')

    def test_idempotency_retries_do_not_start_a_second_job(self):
        key = uuid.uuid4().hex
        job = self.submit('held-owned-once', key).json()
        repeated = self.submit('held-owned-once', key)
        self.assertEqual(repeated.json()['id'], job['id'])
        self.assertEqual(self.submit('different', key).status_code, 409)
        self.assertEqual(self.submit('held-owned-once').status_code, 409)
        self.release('held-owned-once')

    def test_gateway_restart_resumes_pull_with_same_job_id(self):
        model = 'held-owned-restart'
        job = self.submit(model).json()
        self.wait(lambda: self.get(job)['state'] == 'running')
        self.gateway.terminate()
        self.gateway.wait(timeout=8)
        self.wait(lambda: model not in self.client.get(self.backend+'/__transfers').json()['active'])
        self.gateway = self.start_gateway()
        self.wait(lambda: model in self.client.get(self.backend+'/__transfers').json()['active'])
        self.assertEqual(self.get(job)['state'], 'running')
        self.release(model)
        self.wait(lambda: self.get(job)['state'] == 'success')

    def test_delete_keeps_queue_rules_and_survives_client_disconnect(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            inference = pool.submit(self.client.post, self.direct+'/api/generate', json={'model':'held-before-delete', 'stream':False})
            try:
                self.wait(lambda: self.client.get(self.direct+'/status').json()['inflight_requests'] == 1)
                job = self.submit('simulated:1b', kind='delete').json()
                status = self.wait(lambda: (s if (s := self.client.get(self.direct+'/status').json())['queued_requests'] else None))
                self.assertEqual(status['queued_remote_requests'], 1)
                self.assertEqual(self.get(job)['state'], 'running')
            finally:
                self.release('held-before-delete')
            self.assertEqual(inference.result().status_code, 200)
        self.wait(lambda: self.get(job)['state'] == 'success')

    def test_rejected_model_reports_failure(self):
        job = self.submit('reject-transfer').json()
        self.wait(lambda: self.get(job)['state'] == 'failed')
        self.assertNotEqual(self.get(job)['percent'], 100)

    def test_validation_and_discovery(self):
        for payload in [{}, [], {'kind':'pull','model':'bad name','request_key':uuid.uuid4().hex},
                        {'kind':'delete','model':'x','request_key':uuid.uuid4().hex}]:
            self.assertEqual(self.client.post(self.proxy+'/oss/model-jobs', json=payload).status_code, 400)
        self.assertEqual(self.client.post(self.proxy+'/oss/model-jobs', content='x'*8193).status_code, 413)
        self.assertEqual(self.client.get(self.direct+'/oss/model-jobs/missing').status_code, 404)
        self.assertEqual(self.client.get(self.direct+'/help').json()['model_job_policy']['execution'], 'server_owned')


if __name__ == '__main__':
    unittest.main(verbosity=2)
