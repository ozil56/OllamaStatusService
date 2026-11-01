"""Isolated recovery journal safety tests; never touches production data."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from gateway import InstanceLock, Journal
from maintenance import clear_after_backend_stop


class AutomaticMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oss-maintenance-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'backend_url': 'http://127.0.0.1:32199', 'data_dir': str(self.root)}))
        journal = Journal(self.root)
        self.ident, _ = journal.enqueue('fixture', '/api/generate')
        journal.start(self.ident)
        journal.finish(self.ident, 'unknown', 'ReadTimeout: fixture')
        journal.close()

    def row(self):
        with closing(sqlite3.connect(self.root/'requests.sqlite3')) as db:
            return db.execute('SELECT state,reason FROM requests WHERE id=?', (self.ident,)).fetchone()

    def test_automatic_clear_preserves_cause_and_never_replays(self):
        with patch('maintenance.listening_ports', return_value=set()):
            clear_after_backend_stop(self.config, automatic=True)
        state, reason = self.row()
        self.assertEqual(state, 'aborted')
        self.assertEqual(reason, 'backend_stopped_by_watchdog; previous_reason=ReadTimeout: fixture')

    def test_listening_backend_blocks_cleanup(self):
        with patch('maintenance.listening_ports', return_value={32199}):
            with self.assertRaisesRegex(RuntimeError, 'still listening'):
                clear_after_backend_stop(self.config, automatic=True)
        self.assertEqual(self.row(), ('unknown', 'ReadTimeout: fixture'))

    def test_running_gateway_lock_blocks_cleanup(self):
        lock = InstanceLock(self.root)
        try:
            with patch('maintenance.listening_ports', return_value=set()):
                with self.assertRaisesRegex(RuntimeError, 'owns this data'):
                    clear_after_backend_stop(self.config, automatic=True)
            self.assertEqual(self.row(), ('unknown', 'ReadTimeout: fixture'))
        finally:
            lock.close()


if __name__ == '__main__':
    unittest.main()
