"""Real HTTP regression tests for embedded /manage; no real models are used."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owned_model_jobs as fixtures
from owned_model_jobs import port

SELF = "00000000000000000000000000000001"
ROOT = Path(__file__).resolve().parents[1]


class ManageTests(unittest.TestCase):
    setUp = fixtures.OwnedJobsTests.setUp
    start = fixtures.OwnedJobsTests.start
    start_gateway = fixtures.OwnedJobsTests.start_gateway
    wait = fixtures.OwnedJobsTests.wait

    def api(self, path, method='GET', body=None, headers=None):
        h = dict(headers or {})
        if method != 'GET':
            h.setdefault('X-OSS-Token', self.client.get(self.direct+'/manage/api/session').json()['token'])
        return self.client.request(method, self.direct+'/manage/api/'+path, json=body, headers=h)

    def jobs(self):
        return self.api('overview').json()['jobs']

    def job(self, ident):
        return next((j for j in self.jobs() if j['id'] == ident), {})

    def refresh(self):
        self.api('refresh', 'POST', {})

    def wait_job(self, ident, state):
        def observed():
            self.refresh()
            job = self.job(ident)
            return job if job.get('state') == state else None
        return self.wait(observed)

    def second_node(self):
        direct, proxy = port(), port()
        config = json.loads(self.config.read_text())
        config.update(listen_port=direct, proxy_listen_port=proxy, data_dir=str(self.work/'second-data'))
        path = self.work/'second.json'
        path.write_text(json.dumps(config))
        self.start([str(ROOT/'app/run.py'), '--config', str(path)], 'second')
        address = f'http://127.0.0.1:{proxy}'
        self.wait(lambda: self.client.get(address+'/healthz').status_code == 200)
        return address

    def test_page_assets_self_node_and_no_separate_login(self):
        for path in ('/manage', '/manage/', '/manage/app.js', '/manage/style.css', '/manage/favicon.svg', '/manage/api/session'):
            response = self.client.get(self.proxy+path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn('www-authenticate', response.headers)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertIn('/manage/app.js', self.client.get(self.direct+'/manage').text)
        self.assertEqual(self.api('session').json()['authentication'], 'reverse_proxy')
        self.refresh()
        node = self.wait(lambda: next((s for s in self.api('overview').json()['servers'] if s.get('is_self') and s.get('online')), None))
        self.assertEqual(node['id'], SELF)
        self.assertEqual(node['job_controls'], ['pause', 'resume'])
        self.assertEqual(self.api('servers/'+SELF, 'DELETE', {}).status_code, 409)
        self.assertEqual(self.api('servers/'+SELF, 'PUT', {'name':'change'}).status_code, 409)
        self.assertEqual(self.client.get(self.direct+'/manage/missing').status_code, 404)
        self.assertEqual(self.api('local/shutdown', 'POST', {}).status_code, 404)

    def test_csrf_origin_and_body_limits(self):
        self.assertEqual(self.api('refresh','POST',{}, {'X-OSS-Token':'wrong'}).status_code, 403)
        self.assertEqual(self.api('refresh','POST',{}, {'Sec-Fetch-Site':'cross-site'}).status_code, 403)
        self.assertEqual(self.api('refresh','POST',{}, {'Origin':'null'}).status_code, 403)
        self.assertEqual(self.client.options(self.direct+'/manage/api/refresh').status_code, 405)
        self.assertEqual(self.api('refresh','POST',{'x':'a'*17000}).status_code, 413)
        # A conventional proxy may rewrite Host to its upstream: CSRF tokens and
        # Fetch Metadata keep working without trusting forwarded identity headers.
        self.assertEqual(self.api('refresh','POST',{}, {'Origin':'https://fixture.example','Sec-Fetch-Site':'same-origin'}).status_code, 202)
        self.assertEqual(self.client.get(self.direct+'/manage', headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate'}).status_code, 200)
        self.assertEqual(self.client.get(self.direct+'/manage/api/session', headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate'}).status_code, 403)

    def test_self_pull_pause_restart_resume_and_confirmed_delete(self):
        result = self.api('servers/'+SELF+'/pull', 'POST', {'model':'held-progress-manage-self'})
        self.assertEqual(result.status_code, 202, result.text)
        job = result.json()
        self.wait_job(job['id'], 'running')
        remote = self.client.get(self.direct+'/oss/model-jobs/'+job['remote_id']).json()
        self.assertEqual(remote['origin'], 'remote')
        paused = self.api('jobs/'+job['id']+'/pause','POST',{})
        self.assertEqual(paused.json()['state'], 'paused', paused.text)
        self.wait(lambda:'held-progress-manage-self' not in self.client.get(self.backend+'/__transfers').json()['active'])
        self.gateway.terminate(); self.gateway.wait(timeout=8)
        self.gateway = self.start_gateway()
        self.wait_job(job['id'], 'paused')
        resumed = self.api('jobs/'+job['id']+'/resume','POST',{})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.client.post(self.backend+'/__release/held-progress-manage-self')
        self.wait_job(job['id'], 'success')
        self.assertEqual(self.api('servers/'+SELF+'/delete-model','POST',{'model':'simulated:1b'}).status_code, 400)
        removed = self.api('servers/'+SELF+'/delete-model','POST',{'model':'simulated:1b','confirm_model':'simulated:1b'})
        self.assertEqual(removed.status_code, 202, removed.text)
        self.wait_job(removed.json()['id'], 'success')

    def test_remote_job_survives_managing_gateway_exit(self):
        address = self.second_node()
        node = self.api('servers','POST',{'name':'远端测试', 'url':address}).json()
        job = self.api('servers/'+node['id']+'/pull','POST',{'model':'held-progress-managed-peer'}).json()
        self.wait_job(job['id'], 'running')
        self.gateway.terminate(); self.gateway.wait(timeout=8)
        self.assertEqual(self.client.get(address+'/oss/model-jobs/'+job['remote_id']).json()['state'], 'running')
        self.gateway = self.start_gateway()
        self.wait_job(job['id'], 'running')
        self.assertEqual(self.api('jobs/'+job['id']+'/pause','POST',{}).json()['state'], 'paused')
        self.assertEqual(self.api('jobs/'+job['id']+'/resume','POST',{}).status_code, 200)
        self.client.post(self.backend+'/__release/held-progress-managed-peer')
        self.wait_job(job['id'], 'success')
        self.assertEqual(self.api('servers/'+node['id'], 'DELETE', {}).status_code, 200)

    def test_import_validation_dpapi_and_deduplication(self):
        address = self.second_node()
        payload = {'servers':[{'name':'私有节点','url':address,'auth':{'username':'fixture','password':'private-fixture-secret'}}]}
        result = self.api('import-servers','POST',payload)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['added'],1)
        self.assertEqual(self.api('import-servers','POST',payload).json()['skipped'],1)
        overview = self.api('overview')
        self.assertNotIn('private-fixture-secret',overview.text)
        self.assertNotIn('secret',overview.text)
        node = next(s for s in overview.json()['servers'] if not s['is_self'])
        self.assertTrue(node['has_auth'])
        for file in (self.work/'data/manage').glob('manager.sqlite3*'):
            self.assertNotIn(b'private-fixture-secret',file.read_bytes())
        invalid={'servers':[{'name':'valid','url':'http://127.0.0.1:1'},{'name':'bad','url':'file:///C:/'}]}
        self.assertEqual(self.api('import-servers','POST',invalid).status_code,400)
        self.assertEqual(len(self.api('overview').json()['servers']),2)

    def test_existing_basic_proxy_protects_page_assets_and_api(self):
        target, upstream = self.proxy, self.client
        auth = 'Basic '+base64.b64encode(b'nginx:fixture-secret').decode()
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                if self.headers.get('Authorization') != auth:
                    self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="existing nginx"');self.end_headers();return
                response = upstream.get(target+self.path)  # No Authorization forwarded.
                self.send_response(response.status_code)
                self.send_header('Content-Type',response.headers.get('content-type',''))
                self.send_header('Content-Length',str(len(response.content)))
                self.end_headers();self.wfile.write(response.content)
        proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        self.addCleanup(proxy.server_close);self.addCleanup(proxy.shutdown)
        address=f'http://127.0.0.1:{proxy.server_port}'
        for path in ('/manage','/manage/app.js','/manage/api/overview'):
            self.assertEqual(self.client.get(address+path).status_code,401)
            result=self.client.get(address+path,auth=('nginx','fixture-secret'))
            self.assertEqual(result.status_code,200,result.text)
            self.assertNotIn('www-authenticate',result.headers)

    def test_peer_uses_configured_nginx_credentials(self):
        target, upstream = self.second_node(), self.client
        auth = 'Basic '+base64.b64encode(b'peer:existing-peer-password').decode()
        received = []
        class PeerProxy(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def relay(self):
                received.append(self.headers.get('Authorization'))
                if received[-1] != auth:
                    self.send_response(401);self.end_headers();return
                body = self.rfile.read(int(self.headers.get('Content-Length',0)))
                response = upstream.request(self.command,target+self.path,content=body,
                    headers={'Content-Type':'application/json'})
                self.send_response(response.status_code)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(response.content)))
                self.end_headers();self.wfile.write(response.content)
            do_GET = do_POST = relay
        proxy=ThreadingHTTPServer(('127.0.0.1',0),PeerProxy)
        threading.Thread(target=proxy.serve_forever,daemon=True).start()
        self.addCleanup(proxy.server_close);self.addCleanup(proxy.shutdown)
        node = self.api('servers','POST',{'name':'Nginx peer','url':f'http://127.0.0.1:{proxy.server_port}',
            'auth_mode':'basic','username':'peer','password':'existing-peer-password'}).json()
        response = self.api('servers/'+node['id']+'/pull','POST',{'model':'held-progress-basic-peer'},
            {'Authorization':'Basic '+base64.b64encode(b'browser:different-inbound-password').decode()})
        self.assertEqual(response.status_code,202,response.text)
        job = response.json()
        self.wait_job(job['id'],'running')
        self.assertEqual(self.api('jobs/'+job['id']+'/pause','POST',{}).json()['state'],'paused')
        self.assertTrue(received)
        self.assertTrue(all(value == auth for value in received))
        self.assertNotIn('existing-peer-password',self.api('overview').text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
