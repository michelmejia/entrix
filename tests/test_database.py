from contextlib import closing
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backup import backup_database, backup_postgres
from database import Row, connect, postgres_sql
from data_checks import verify_copy
from migrate_neon import snapshot_source
from server import create_app, init_db


class DatabaseTests(unittest.TestCase):
    def test_row_contract_and_numeric_json_values(self):
        row = Row(('income', 'name'), (Decimal('100000000000'), 'Cliente'))
        self.assertEqual(row[0], 100000000000)
        self.assertEqual(row['name'], 'Cliente')
        self.assertEqual(dict(row), {'income': 100000000000, 'name': 'Cliente'})
        self.assertEqual(list(row), [100000000000, 'Cliente'])

    def test_placeholders_do_not_modify_literals_or_data(self):
        sql = "SELECT '?', '50%', instr(lower(name),lower(?)) FROM t WHERE id=?"
        self.assertEqual(postgres_sql(sql, ('? %', 'x')),
                         "SELECT '?', '50%%', strpos(lower(name),lower(%s)) FROM t WHERE id=%s")
        self.assertEqual(postgres_sql("SELECT 'instr(?) 50%'", None), "SELECT 'instr(?) 50%'")

    def test_render_refuses_silent_sqlite_fallback(self):
        with patch.dict(os.environ, {'RENDER': 'true', 'DATABASE_URL': ''}):
            with self.assertRaises(RuntimeError):
                create_app()
            self.assertEqual(create_app('explicit-test.db').config['DATABASE'], 'explicit-test.db')

    def test_snapshot_backup_round_trip_and_corruption_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.db'
            init_db(create_app(source))
            with closing(connect(source)) as c:
                c.execute("INSERT INTO users VALUES('u','admin','Admin','password-hash','admin',1)")
                c.execute("INSERT INTO events(id,name,created_at) VALUES('e','Evento','2026')")
                c.execute("INSERT INTO tickets(id,event_id,token,created_at,ticket_number,price_cents,paid_cents) VALUES('t','e','unchanged-qr','2026',1,100000000000,2340)")
                # Duplicate rows without a primary key must also be preserved.
                c.execute("INSERT INTO login_attempts VALUES('admin','local',1.25)")
                c.execute("INSERT INTO login_attempts VALUES('admin','local',1.25)")
            snapshot = snapshot_source(source)
            with closing(connect(source)) as original, closing(connect(snapshot)) as copied:
                self.assertEqual(verify_copy(original, copied)['login_attempts'], 2)
                copied.execute("UPDATE tickets SET token='corrupt' WHERE id='t'")
                with self.assertRaisesRegex(RuntimeError, 'tickets'):
                    verify_copy(original, copied)
                self.assertEqual(original.execute('SELECT token FROM tickets').fetchone()[0], 'unchanged-qr')

            # Exercise the portable backup writer against a locked connection;
            # the PostgreSQL suite below exercises its real PostgreSQL source.
            portable = Path(directory) / 'portable.db'
            with closing(connect(source)) as original:
                original.execute('BEGIN IMMEDIATE')
                backup_postgres(original, portable)
                self.assertTrue(original.in_transaction)
                original.execute('ROLLBACK')
            with closing(connect(source)) as original, closing(connect(portable)) as restored:
                self.assertEqual(verify_copy(original, restored)['tickets'], 1)
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_incompatible_source_is_rejected_without_altering_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.db'
            init_db(create_app(source))
            with closing(connect(source)) as c:
                c.execute('CREATE TABLE unknown_data(id TEXT)')
            with self.assertRaisesRegex(RuntimeError, 'desconocidas'):
                snapshot_source(source)
            with closing(connect(source)) as c:
                self.assertIsNotNone(c.execute("SELECT name FROM sqlite_master WHERE name='unknown_data'").fetchone())


if __name__ == '__main__':
    unittest.main()
