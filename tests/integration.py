"""Run with bundled runtime. Uses free loopback ports, temporary data, fake Ollama.
Does not register services, change the firewall or contact a real Ollama instance.
"""
import concurrent.futures
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
from gateway import Settings, TerminalDetector

ROOT = Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='ollama-status-test-')
        cls.work = Path(cls.temp.name)
        cls.backend_port, cls.port = free_port(), free_port()
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.backend = f'http://127.0.0.1:{cls.backend_port}'
        cls.client = httpx.Client(base_url=cls.base, timeout=10, trust_env=False)
        cls.children = []
        cls.logfiles = []
        cls.config = cls.work / 'config.json'
        cls.config.write_text(json.dumps(dict(backend_url=cls.backend, listen_host='127.0.0.1',
            listen_port=cls.port, proxy_listen_port=free_port(), data_dir=str(cls.work/'data'), health_interval_seconds=.1,
            health_timeout_seconds=.3, request_deadline_seconds=4,
            upstream_read_timeout_seconds=3, client_write_timeout_seconds=.3,
            max_queue_size=2, max_queue_bytes=4096, queue_timeout_seconds=2,
            shutdown_grace_seconds=.3)), encoding='utf-8')
        cls.backproc = cls.launch([str(ROOT/'tests'/'fake_backend.py'),'--port',str(cls.backend_port)],'backend')
        cls.wait_url(cls.backend + '/api/version')
        cls.start_gateway()

    @classmethod
    def launch(cls, args, name):
        log = open(cls.work/(name+'-'+str(len(cls.children))+'.log'), 'wb')
        cls.logfiles.append(log)
        p = subprocess.Popen([sys.executable] + args, stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        cls.children.append(p)
        return p

    @classmethod
    def start_gateway(cls):
        cls.gateway = cls.launch([str(ROOT/'app'/'run.py'),'--config',str(cls.config)],'gateway')
        cls.wait_url(cls.base+'/healthz')

    @classmethod
    def wait_url(cls,url):
        for _ in range(100):
            try:
                r=httpx.get(url,timeout=.3,trust_env=False)
                if r.status_code==200: return
            except httpx.HTTPError: pass
            time.sleep(.05)
        raise AssertionError('Service failed to start: '+url)

    def wait_state(self, state, timeout=6):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            data=self.client.get('/status').json()
            if data['state']==state: return data
            time.sleep(.05)
        self.fail(f'Expected {state}; last status {data}')

    def reset_unknown(self):
        # Maintenance is allowed only after BOTH test processes stop; mimic Manage.ps1.
        self.gateway.terminate(); self.gateway.wait(timeout=5)
        self.backproc.terminate(); self.backproc.wait(timeout=5)
        result=subprocess.run([sys.executable,str(ROOT/'app'/'maintenance.py'),'--config',str(self.config),'--backend-stopped'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        type(self).backproc=self.launch([str(ROOT/'tests'/'fake_backend.py'),'--port',str(self.backend_port)],'backend')
        self.wait_url(self.backend+'/api/version')
        type(self).start_gateway()
        self.wait_state('idle')

    @classmethod
    def tearDownClass(cls):
        for child in reversed(cls.children):
            if child.poll() is None:
                child.terminate(); child.wait(timeout=5)
        cls.client.close()
        for file in cls.logfiles: file.close()
        # Copy verification logs beside the build, not into deployment runtime.
        import shutil
        evidence=ROOT.parent/'OllamaStatusService-build'/'test-logs'
        evidence.mkdir(parents=True, exist_ok=True)
        for log in cls.work.glob('*.log'): shutil.copyfile(log,evidence/log.name)
        cls.temp.cleanup()

    def test_01_metadata_and_nonstream(self):
        self.wait_state('idle')
        self.assertEqual(self.client.get('/api/tags').json()['models'][0]['name'],'simulated:1b')
        self.assertEqual(self.client.post('/api/show',json={'model':'fast'}).status_code,200)
        response=self.client.post('/api/generate',json={'model':'fast','stream':False})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['done'])
        self.assertIn('x-request-id',response.headers)
        self.wait_state('idle')

    def wait_queue(self, count, timeout=3):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=self.client.get('/status').json()
            if value['queued_requests']==count: return value
            time.sleep(.03)
        self.fail(f'Expected queue {count}: {value}')

    def test_02_fifo_same_model_and_capacity(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            first=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
            self.wait_state('busy')
            before=httpx.get(self.backend+'/__received',trust_env=False).json()['count']
            second=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
            queued=self.wait_queue(1)
            self.assertTrue(queued['accepting_requests'])
            self.assertFalse(queued['can_start_now'])
            self.assertEqual(httpx.get(self.backend+'/__received',trust_env=False).json()['count'],before)
            third=pool.submit(self.client.post,'/api/generate',json={'model':'fast','stream':False},headers={'X-Ollama-Queue-Timeout':'5'})
            self.wait_queue(2)
            rejected=self.client.post('/api/chat',json={'model':'fast'})
            self.assertEqual(rejected.status_code,429)
            self.assertEqual(rejected.json()['error'],'queue_full')
            self.assertEqual(first.result().status_code,200)
            self.assertEqual(second.result().status_code,200)
            self.assertEqual(third.result().status_code,200)
            history=httpx.get(self.backend+'/__received',trust_env=False).json()['models']
            self.assertEqual(history[-3:],['slow','slow','fast'])
        self.wait_state('idle')

    def test_03_client_disconnect_preserves_busy(self):
        with self.client.stream('POST','/api/chat',json={'model':'slow'}) as r:
            self.assertEqual(r.status_code,200)
            self.assertIn('start',next(r.iter_lines()))
        time.sleep(.15)
        self.assertEqual(self.client.get('/status').json()['state'],'busy')
        self.wait_state('idle')

    def test_04_streaming_and_openai(self):
        r=self.client.post('/api/chat',json={'model':'fast'})
        rows=[json.loads(line) for line in r.text.splitlines()]
        self.assertTrue(rows[-1]['done'])
        self.wait_state('idle')
        r=self.client.post('/v1/chat/completions',json={'model':'fast','stream':True})
        self.assertIn('data: [DONE]',r.text)
        self.wait_state('idle')
        self.assertEqual(self.client.post('/v1/completions',json={'model':'fast'}).status_code,200)
        self.wait_state('idle')

    def test_05_embedding_endpoints(self):
        for endpoint,key in [('/api/embed','embeddings'),('/api/embeddings','embedding'),('/v1/embeddings','data')]:
            result=self.client.post(endpoint,json={'model':'fast','input':'test'})
            self.assertEqual(result.status_code,200)
            self.assertIn(key,result.json())
            self.wait_state('idle')

    def test_06_upstream_rejection_and_error(self):
        self.assertEqual(self.client.post('/api/chat',json={'model':'reject'}).status_code,404)
        self.wait_state('idle')
        r=self.client.post('/api/chat',json={'model':'error-stream'})
        self.assertIn('simulated runner error',r.text)
        self.wait_state('idle')

    def test_07_truncated_stream_blocks_admission(self):
        r=self.client.post('/api/chat',json={'model':'truncated'})
        self.assertIn('unconfirmed',r.text)
        self.assertEqual(self.wait_state('unknown')['uncertain_requests'],1)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'}).status_code,503)
        self.reset_unknown()

    def test_08_crash_recovery_is_not_false_idle(self):
        with self.client.stream('POST','/api/chat',json={'model':'slow'}) as r:
            next(r.iter_lines())
            self.wait_state('busy')
            self.gateway.kill(); self.gateway.wait(timeout=5)
        type(self).start_gateway()
        self.assertEqual(self.wait_state('unknown')['uncertain_requests'],1)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'}).status_code,503)
        self.reset_unknown()

    def test_09_second_process_refuses_shared_state(self):
        p=self.launch([str(ROOT/'app'/'run.py'),'--config',str(self.config)],'duplicate')
        self.assertNotEqual(p.wait(timeout=10),0)
        self.wait_state('idle')

    def test_10_bad_response_and_deadline(self):
        self.assertEqual(self.client.post('/api/chat',json={'model':'invalid','stream':False}).status_code,502)
        self.wait_state('unknown'); self.reset_unknown()
        r=self.client.post('/api/chat',json={'model':'hang'})
        self.assertIn('unconfirmed',r.text)
        self.wait_state('unknown'); self.reset_unknown()

    def test_11_invalid_requests_do_not_admit(self):
        self.assertEqual(self.client.post('/api/chat',json={'stream':False}).status_code,400)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast','stream':'false'}).status_code,400)
        self.assertEqual(self.client.post('/api/chat',content=b'not-json').status_code,400)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'nan'}).status_code,400)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'-1'}).status_code,400)
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'999999'}).status_code,400)
        self.wait_state('idle')

    def test_12_offline_and_maintenance_guard(self):
        result=subprocess.run([sys.executable,str(ROOT/'app'/'maintenance.py'),'--config',str(self.config),'--backend-stopped'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode,0)
        self.backproc.terminate(); self.backproc.wait(timeout=5)
        self.wait_state('offline')
        self.assertEqual(self.client.post('/api/chat',json={'model':'fast'}).status_code,503)
        type(self).backproc=self.launch([str(ROOT/'tests'/'fake_backend.py'),'--port',str(self.backend_port)],'backend')
        self.wait_url(self.backend+'/api/version'); self.wait_state('idle')

    def test_13_configuration(self):
        with self.assertRaises(ValueError): Settings(max_concurrency=2).validate()
        with self.assertRaises(ValueError): Settings(backend_url='http://example.com:11434').validate()
        detector=TerminalDetector(); detector.feed(b'{"done":tr'); detector.feed(b'ue}\n'); self.assertTrue(detector.terminal)

    def test_14_windows_listener_table_ipv4_and_ipv6(self):
        from maintenance import listening_ports
        for family, address in [(socket.AF_INET,'127.0.0.1'),(socket.AF_INET6,'::1')]:
            with socket.socket(family,socket.SOCK_STREAM) as listener:
                listener.bind((address,0)); listener.listen()
                port=listener.getsockname()[1]
                self.assertIn(port,listening_ports())
            self.assertNotIn(port,listening_ports())

    def test_15_immediate_busy_and_client_chosen_deadline(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
            self.wait_state('busy')
            before=httpx.get(self.backend+'/__received',trust_env=False).json()['count']
            start=time.monotonic()
            r=self.client.post('/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'0'})
            self.assertEqual(r.status_code,429)
            self.assertEqual(r.headers['x-ollama-forwarded'],'false')
            self.assertLess(time.monotonic()-start,.6)
            start=time.monotonic()
            r=self.client.post('/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'.25'})
            self.assertEqual(r.status_code,429)
            self.assertEqual(r.json()['error'],'queue_wait_timeout')
            self.assertFalse(r.json()['forwarded'])
            self.assertGreater(time.monotonic()-start,.2)
            self.assertLess(time.monotonic()-start,1)
            self.wait_queue(0)
            self.assertEqual(httpx.get(self.backend+'/__received',trust_env=False).json()['count'],before)
            self.assertEqual(first.result().status_code,200)
        r=self.client.post('/api/generate',json={'model':'fast','stream':False},headers={'X-Ollama-Queue-Timeout':'0'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers['x-ollama-forwarded'],'true')
        self.wait_state('idle')

    def test_16_queued_disconnect_never_executes(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
            self.wait_state('busy')
            before=httpx.get(self.backend+'/__received',trust_env=False).json()['count']
            with socket.create_connection(('127.0.0.1',self.port)) as sock:
                payload=b'{"model":"fast","stream":false}'
                sock.sendall(f'POST /api/generate HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n'.encode()+payload)
                self.wait_queue(1)
            self.wait_queue(0)
            self.assertEqual(first.result().status_code,200)
            self.assertEqual(httpx.get(self.backend+'/__received',trust_env=False).json()['count'],before)
        self.wait_state('idle')

    def test_17_default_queue_deadline_and_uncertain_flush(self):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first=pool.submit(self.client.post,'/api/chat',json={'model':'hang'})
            self.wait_state('busy')
            start=time.monotonic()
            response=self.client.post('/api/chat',json={'model':'fast'})
            self.assertEqual(response.status_code,429)
            self.assertEqual(response.json()['error'],'queue_wait_timeout')
            self.assertGreater(time.monotonic()-start,1.8)
            self.assertLess(time.monotonic()-start,2.8)
            second=pool.submit(self.client.post,'/api/chat',json={'model':'fast'},headers={'X-Ollama-Queue-Timeout':'5'})
            self.wait_queue(1)
            first.result()
            self.assertEqual(second.result().status_code,503)
            self.assertEqual(self.wait_state('unknown')['queued_requests'],0)
        self.reset_unknown()

    def test_18_queue_byte_limit(self):
        r=self.client.post('/api/chat',json={'model':'fast','prompt':'x'*4500})
        self.assertEqual(r.status_code,429)
        self.assertEqual(r.json()['error'],'queue_full')
        self.assertFalse(r.json()['forwarded'])
        self.wait_state('idle')

    def test_19_passthrough_compatibility(self):
        self.assertEqual(self.client.get('/').text,'Ollama is running')
        self.assertIn('Ollama',self.client.get('/dashboard').text)
        self.assertEqual(self.client.head('/').status_code,200)
        r=self.client.options('/api/chat',headers={'Origin':'http://localhost','Access-Control-Request-Method':'POST'})
        self.assertEqual(r.headers['access-control-allow-origin'],'http://localhost')
        for method,path in [('POST','/api/pull'),('POST','/api/create'),('POST','/api/copy'),('DELETE','/api/delete')]:
            r=self.client.request(method,path,json={'model':'fast'},headers={'Authorization':'Bearer test-value'})
            self.assertEqual(r.status_code,200)
            self.wait_state('idle')
        r=self.client.get('/v1/models/fast?detail=1',headers={'Authorization':'Bearer test-value'})
        self.assertEqual(r.json(),{'query':'detail=1','auth':'Bearer test-value'})
        payload=b'x'*10000
        r=self.client.post('/api/blobs/sha256:fake',content=payload)
        self.assertEqual(r.status_code,201)
        self.assertEqual(r.json()['bytes'],len(payload))

    def test_20_responses_and_anthropic(self):
        for endpoint in ['/v1/responses','/v1/messages']:
            for stream in [False,True]:
                r=self.client.post(endpoint,json={'model':'fast','stream':stream})
                self.assertEqual(r.status_code,200,r.text)
                self.wait_state('idle')

    def test_21_crash_aborts_queue_without_replay(self):
        import sqlite3
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
            self.wait_state('busy')
            second=pool.submit(self.client.post,'/api/generate',json={'model':'fast','stream':False})
            state=self.wait_queue(1)
            queued_id=next(row['id'] for row in state['requests'] if row['state']=='queued')
            self.gateway.kill(); self.gateway.wait(timeout=5)
            for future in [first,second]:
                try: future.result()
                except httpx.HTTPError: pass
        type(self).start_gateway()
        self.assertEqual(self.wait_state('unknown')['queued_requests'],0)
        from contextlib import closing
        with closing(sqlite3.connect(self.work/'data'/'requests.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT state FROM requests WHERE id=?',(queued_id,)).fetchone()[0],'aborted')
        self.reset_unknown()

    def test_22_multiserver_client_race_and_no_replay(self):
        import io
        import urllib.request
        from unittest.mock import patch
        sys.path.insert(0,str(ROOT/'examples'))
        from route_request import call_servers
        port=free_port()
        base2=f'http://127.0.0.1:{port}'
        conf=json.loads(self.config.read_text())
        conf.update(listen_port=port,proxy_listen_port=free_port(),data_dir=str(self.work/'data2'))
        config2=self.work/'config2.json'
        config2.write_text(json.dumps(conf))
        process=self.launch([str(ROOT/'app'/'run.py'),'--config',str(config2)],'second-gateway')
        self.wait_url(base2+'/status')
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                first=pool.submit(self.client.post,'/api/generate',json={'model':'slow','stream':False})
                self.wait_state('busy')
                # Make the status hint stale: A1 reports idle just before another task gets admitted.
                real_opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                class StaleProbe:
                    def open(inner,request,**kwargs):
                        if isinstance(request,str) and request.endswith('/status'):
                            return io.BytesIO(b'{"can_start_now":true,"accepting_requests":true}')
                        return real_opener.open(request,**kwargs)
                with patch('urllib.request.build_opener',return_value=StaleProbe()):
                    server,result=call_servers([self.base,base2],'fast','test',queue_seconds=0,response_timeout=10)
                self.assertEqual(server,base2)
                self.assertTrue(result['done'])
                self.assertEqual(first.result().status_code,200)
            before=httpx.get(self.backend+'/__received',trust_env=False).json()['count']
            with self.assertRaisesRegex(RuntimeError,'automatic replay disabled'):
                call_servers([self.base,base2],'reject','test',response_timeout=10)
            self.assertEqual(httpx.get(self.backend+'/__received',trust_env=False).json()['count'],before+1)
        finally:
            process.terminate(); process.wait(timeout=5)
        self.wait_state('idle')

    def test_23_no_queue_admission_is_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.client.post('/api/generate',json={'model':'slow','stream':False},
                              headers={'X-Ollama-Queue-Timeout':'0'}).status_code,range(8)))
        self.assertEqual(results.count(200),1)
        self.assertEqual(results.count(429),7)
        self.wait_state('idle')

    def test_24_agent_discovery_reflects_live_config(self):
        response=self.client.get('/')
        self.assertEqual(response.headers['x-ollama-status-service'],'1.2.3')
        self.assertIn('</help>',response.headers['link'])
        help_data=self.client.get('/help').json()
        self.assertEqual(help_data['queue_policy']['default'],2)
        self.assertEqual(help_data['queue_policy']['max_waiting_requests'],2)
        self.assertEqual(help_data['queue_policy']['max_waiting_body_bytes'],4096)
        self.assertFalse(help_data['configuration']['remote_configuration_api'])
        self.assertEqual(self.client.get('/.well-known/ollama-status-service').json(),help_data)
        schema=self.client.get('/openapi.json').json()
        self.assertEqual(schema['openapi'],'3.1.0')
        param=schema['paths']['/api/generate']['post']['parameters'][0]
        self.assertEqual(param['schema']['default'],2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
