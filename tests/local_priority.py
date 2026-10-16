"""Exercise the real ASGI queue, journal and dispatcher with controlled upstream completion.
Socket origins are provided by ASGI transports; no real services or firewall are changed.
"""
import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx
from starlette.requests import Request
from gateway import Settings, client_origin, create_app

HTTPClient = httpx.AsyncClient


class OriginTests(unittest.TestCase):
    def origin(self, peer, server='192.0.2.10', headers=(), port=11434, proxy_port=11436):
        return client_origin(Request({'type': 'http', 'client': (peer, 1234) if peer else None,
                                      'server': (server, port), 'headers': list(headers)}), proxy_port)

    def test_proxy_listener_forces_remote_for_all_peers_and_headers(self):
        for peer in ('127.0.0.1', '127.0.0.2', '::1', '::ffff:127.0.0.1', '192.0.2.10', '198.51.100.20'):
            with self.subTest(peer=peer):
                self.assertEqual(self.origin(peer, port=11436, headers=[
                    (b'host', b'127.0.0.1:11434'), (b'x-forwarded-for', b'127.0.0.1'),
                    (b'x-ollama-priority', b'local'), (b'origin', b'local')]), 'remote')
        self.assertEqual(self.origin('127.0.0.1', port=19136, proxy_port=19136), 'remote')

    def test_socket_origins(self):
        for peer in ('127.0.0.1', '127.0.0.2', '::1', '::ffff:127.0.0.1', '192.0.2.10'):
            self.assertEqual(self.origin(peer), 'local', peer)
        for peer in ('198.51.100.20', '192.168.1.8', '::ffff:198.51.100.20', None, 'invalid', 'localhost'):
            self.assertEqual(self.origin(peer), 'remote', peer)
        self.assertEqual(self.origin('fe80::1%4', 'fe80::1%4'), 'local')

    def test_ipv6_and_mapped_addresses_preserve_local_priority_boundary(self):
        self.assertEqual(self.origin('2001:db8::133', '2001:db8::133'), 'local')
        self.assertEqual(self.origin('2001:db8::42', '2001:db8::133'), 'remote')
        self.assertEqual(self.origin('::ffff:192.168.2.133', '192.168.2.133'), 'local')
        self.assertEqual(self.origin('192.168.2.133', '::ffff:192.168.2.133'), 'local')
        self.assertEqual(self.origin('2001:db8::42', '2001:db8::133', headers=[
            (b'x-forwarded-for', b'::1'), (b'forwarded', b'for="[::1]"'),
            (b'x-ollama-priority', b'local')]), 'remote')

    def test_headers_cannot_claim_local(self):
        self.assertEqual(self.origin('198.51.100.20', headers=[
            (b'x-forwarded-for', b'127.0.0.1'), (b'forwarded', b'for=127.0.0.1'),
            (b'host', b'127.0.0.1:11434'), (b'x-ollama-priority', b'local')]), 'remote')


class PriorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ollama-priority-')
        self.received = []
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.tasks = []
        async def upstream(request):
            if request.url.path.endswith('/api/version'):
                return httpx.Response(200, json={'version': 'fake'})
            model = json.loads(request.content)['model']
            self.received.append(model)
            if model == 'active':
                self.started.set()
                await self.release.wait()
            return httpx.Response(200, json={'model': model, 'done': True, 'response': 'ok'})
        def factory(**kwargs):
            return HTTPClient(transport=httpx.MockTransport(upstream), **kwargs)
        self.app = create_app(Settings(data_dir=self.temp.name, max_queue_size=10,
                                      queue_timeout_seconds=5, health_interval_seconds=.1))
        self.lifetime = self.app.router.lifespan_context(self.app)
        with patch('gateway.httpx.AsyncClient', side_effect=factory):
            await self.lifetime.__aenter__()
        self.local = HTTPClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 444)), base_url='http://192.0.2.10:11434')
        self.remote = HTTPClient(transport=httpx.ASGITransport(app=self.app, client=('198.51.100.20', 555)), base_url='http://192.0.2.10:11434')

    async def asyncTearDown(self):
        self.release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.lifetime.__aexit__(None, None, None)
        await self.local.aclose()
        await self.remote.aclose()
        self.temp.cleanup()

    def submit(self, client, model, headers=None):
        task = asyncio.create_task(client.post('/api/generate', json={'model': model, 'stream': False}, headers=headers))
        self.tasks.append(task)
        return task

    async def queued(self, number):
        for _ in range(200):
            value = (await self.local.get('/status')).json()
            if value['queued_requests'] == number:
                return value
            await asyncio.sleep(.01)
        self.fail(f'Expected {number} queued requests, got {value}')

    async def hold_remote(self):
        task = self.submit(self.remote, 'active')
        await asyncio.wait_for(self.started.wait(), 3)
        return task

    async def test_nonpreemptive_local_priority_and_both_fifos(self):
        active = await self.hold_remote()
        r1 = self.submit(self.remote, 'remote-1'); await self.queued(1)
        l1 = self.submit(self.local, 'local-1'); await self.queued(2)
        r2 = self.submit(self.remote, 'remote-2'); await self.queued(3)
        l2 = self.submit(self.local, 'local-2'); status = await self.queued(4)
        self.assertEqual(status['queued_local_requests'], 2)
        self.assertEqual(status['queued_remote_requests'], 2)
        self.assertEqual(status['active_origin'], 'remote')
        self.assertEqual(self.received, ['active'])
        self.release.set()
        results = await asyncio.wait_for(asyncio.gather(active, r1, l1, r2, l2), 5)
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(self.received, ['active', 'local-1', 'local-2', 'remote-1', 'remote-2'])

    async def test_remote_fifo_without_local_or_reservation(self):
        await self.submit(self.local, 'local-finished')
        active = await self.hold_remote()
        r1 = self.submit(self.remote, 'remote-1'); await self.queued(1)
        r2 = self.submit(self.remote, 'remote-2'); await self.queued(2)
        self.release.set()
        await asyncio.gather(active, r1, r2)
        self.assertEqual(self.received, ['local-finished', 'active', 'remote-1', 'remote-2'])

    async def test_spoofed_headers_remain_remote(self):
        active = await self.hold_remote()
        spoof = self.submit(self.remote, 'spoof', {'X-Forwarded-For': '127.0.0.1', 'Forwarded': 'for=127.0.0.1', 'X-Ollama-Priority': 'local'})
        await self.queued(1)
        local = self.submit(self.local, 'local'); status = await self.queued(2)
        self.assertEqual(status['queued_local_requests'], 1)
        self.release.set()
        await asyncio.gather(active, spoof, local)
        self.assertEqual(self.received, ['active', 'local', 'spoof'])

    async def test_expired_local_does_not_hold_slot(self):
        active = await self.hold_remote()
        remote = self.submit(self.remote, 'remote'); await self.queued(1)
        local = self.submit(self.local, 'expired', {'X-Ollama-Queue-Timeout': '.05'})
        response = await asyncio.wait_for(local, 2)
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()['forwarded'])
        self.assertEqual((await self.queued(1))['queued_local_requests'], 0)
        self.release.set()
        await asyncio.gather(active, remote)
        self.assertEqual(self.received, ['active', 'remote'])

    async def test_cancelled_local_does_not_hold_slot(self):
        active = await self.hold_remote()
        local = self.submit(self.local, 'cancelled'); await self.queued(1)
        local.cancel()
        await asyncio.gather(local, return_exceptions=True)
        await self.queued(0)
        remote = self.submit(self.remote, 'remote'); await self.queued(1)
        self.release.set()
        await asyncio.gather(active, remote)
        self.assertEqual(self.received, ['active', 'remote'])

    async def test_zero_wait_remains_explicit_for_both_origins(self):
        active = await self.hold_remote()
        for client in (self.local, self.remote):
            response = await self.submit(client, 'zero', {'X-Ollama-Queue-Timeout': '0'})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['error'], 'busy_no_queue')
        await self.queued(0)
        self.release.set()
        await active
        self.assertEqual(self.received, ['active'])

    async def test_help_describes_priority_without_leases(self):
        value = (await self.local.get('/help')).json()['queue_policy']
        self.assertEqual(value['ordering'], 'local_first_fifo_per_origin')
        self.assertFalse(value['priority']['preempt_running'])
        self.assertFalse(value['priority']['reservation_supported'])
        self.assertTrue(value['priority']['zero_timeout_applies_to_local'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
