"""Test the actual preflight process on a legacy-format SQLite/config fixture."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UpgradeValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ollama-upgrade-validation-')
        self.folder = Path(self.temp.name)
        self.config = self.folder/'config.json'
        self.db = self.folder/'requests.sqlite3'
        self.values = {'data_dir': str(self.folder), 'queue_timeout_seconds': 71}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE requests(id TEXT PRIMARY KEY, model TEXT NOT NULL, endpoint TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, reason TEXT)')
            db.execute("INSERT INTO requests VALUES('old-task','test','/api/chat','done','2026-10-13',NULL,NULL)")

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        self.config.write_text(json.dumps(self.values), encoding='utf-8-sig')
        config_bytes, db_bytes = self.config.read_bytes(), self.db.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT/'app'/'validate_upgrade.py'), '--config', str(self.config), '--check-idle'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(self.config.read_bytes(), config_bytes)
        self.assertEqual(self.db.read_bytes(), db_bytes)
        return result.returncode

    def test_old_idle_config_and_history_pass_without_migration(self):
        self.assertEqual(self.check(), 0)

    def test_active_queued_unknown_are_refused_without_clearing(self):
        for state in ('running', 'queued', 'unknown'):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.db)) as db, db:
                    db.execute('UPDATE requests SET state=?', (state,))
                self.assertNotEqual(self.check(), 0)

    def test_invalid_config_is_refused_before_uninstall(self):
        self.values['max_concurrency'] = 2
        self.assertNotEqual(self.check(), 0)

    def test_legacy_ipv4_and_new_ipv6_options(self):
        self.values['listen_host'] = '0.0.0.0'
        self.assertEqual(self.check(), 0)
        for enabled in (True, False):
            self.values['listen_ipv6'] = enabled
            self.assertEqual(self.check(), 0)
        self.values['listen_ipv6'] = 'false'
        self.assertNotEqual(self.check(), 0)

    def test_unknown_config_is_not_silently_discarded(self):
        self.values['unknown_custom_setting'] = True
        self.assertNotEqual(self.check(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
