"""Integration tests: ENTRIX_TEST_DATABASE_URL must be a direct test DB URL.

Each test owns a random schema; no existing schemas or records are deleted.
"""
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import test_server as existing
from backup import backup_postgres
from database import connect, is_postgres
from data_checks import verify_copy
from migrate_neon import migrate
from server import create_app, init_db

TEST_URL = os.environ.get('ENTRIX_TEST_DATABASE_URL', '')


@unittest.skipUnless(TEST_URL and importlib.util.find_spec('psycopg'),
                     'Requires psycopg and ENTRIX_TEST_DATABASE_URL (direct connection).')
class PostgresTests(unittest.TestCase):
    login = existing.AccessTests.login
    post = existing.AccessTests.post
    event = existing.AccessTests.event
    sale = existing.AccessTests.sale
    worker = existing.AccessTests.worker
    prepare_reset = existing.AccessTests.prepare_reset

    def setUp(self):
        import psycopg
        from psycopg import sql
        if not is_postgres(TEST_URL):
            self.fail('ENTRIX_TEST_DATABASE_URL must be a PostgreSQL URL.')
        self.schema = 'entrix_test_' + secrets.token_hex(10)
        admin = psycopg.connect(TEST_URL, autocommit=True)
        self.addCleanup(admin.close)
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(lambda: admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema))))
        parsed = urlsplit(TEST_URL)
        query = dict(parse_qsl(parsed.query))
        query['options'] = '-csearch_path=' + self.schema
        self.url = urlunsplit(parsed._replace(query=urlencode(query)))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(os.environ, {'ACCESO_BACKUP_DIR': self.temp.name, 'RENDER': '', 'ACCESO_HTTPS': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.path = Path(self.temp.name) / 'source.db'
        self.app = create_app(self.url)
        self.app.config['TESTING'] = True
        init_db(self.app)
        self.admin = self.app.test_client()
        result = self.admin.post('/api/auth/setup', json={'setupCode': self.app.config['SETUP_CODE'],
            'username': 'admin', 'name': 'Admin', 'password': existing.PASSWORD}, headers={'X-Requested-With': 'Acceso'})
        self.assertEqual(result.status_code, 200, result.json)
        self.csrf = self.login(self.admin)

    def test_backup_migration_round_trip_and_refuse_nonempty_destination(self):
        eid, rate = self.event()
        self.sale(eid, rate, 3)
        with closing(connect(self.url)) as source:
            backup_postgres(source, self.path)
            with closing(connect(self.path)) as copied:
                self.assertEqual(verify_copy(source, copied)['tickets'], 3)
        with self.assertRaisesRegex(RuntimeError, 'ya contiene datos'):
            migrate(self.path, self.url, apply=True)
        # Only this test's isolated schema is cleared, to test a complete import.
        from database import TABLES
        with closing(connect(self.url)) as target:
            target.execute('BEGIN IMMEDIATE')
            for table in reversed(TABLES):
                target.execute('DELETE FROM "' + table + '"')
            target.execute('COMMIT')
        self.assertEqual(migrate(self.path, self.url, apply=False)['tickets'], 3)
        with closing(connect(self.url)) as target:
            self.assertEqual(target.execute('SELECT count(*) FROM users').fetchone()[0], 0)
        self.assertEqual(migrate(self.path, self.url, apply=True)['tickets'], 3)
        self.csrf = self.login(self.admin)
        self.assertEqual(self.admin.get('/api/cash').json['totals'][0]['income'], 3000000)

    def test_large_amounts_search_and_ticket_image(self):
        r = self.post('/api/events', {'name': 'Grande', 'capacity': 5, 'initialQty': 1,
            'tariffs': [{'name': 'General', 'price': 1000000000}]})
        self.assertEqual(r.status_code, 200, r.json)
        eid = r.json['event']['id']
        rate = self.admin.get('/api/bootstrap').json['tariffs'][0]['id']
        tr = self.admin.get('/api/tickets?eventId=' + eid).json['tickets'][0]
        r = self.post('/api/tickets/' + tr['id'] + '/sale', {'tariffId': rate,
            'initialPayment': 1000000000, 'customerName': "Cliente O'Brien 100%"})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(self.admin.get('/api/cash').json['totals'][0]['income'], 100000000000)
        self.assertEqual(self.admin.get('/api/tickets', query_string={'eventId': eid, 'q': "o'brien"}).json['total'], 1)
        image = self.admin.get('/api/tickets/' + tr['id'] + '/image.jpg')
        self.assertEqual(image.status_code, 200)
        image.close()

    def test_failed_import_rolls_back_all_rows(self):
        from database import TABLES
        import sqlite3
        init_db(create_app(self.path))
        with closing(connect(self.path)) as source:
            source.execute('PRAGMA foreign_keys=OFF')
            source.execute("INSERT INTO users VALUES('u','test','Test','hash','admin',1)")
            source.execute("INSERT INTO tariffs VALUES('bad','missing-event','Bad',100)")
        with closing(connect(self.url)) as target:
            target.execute('BEGIN IMMEDIATE')
            for table in reversed(TABLES):
                target.execute('DELETE FROM "' + table + '"')
            target.execute('COMMIT')
        with self.assertRaises(sqlite3.IntegrityError):
            migrate(self.path, self.url, apply=True)
        with closing(connect(self.url)) as target:
            for table in TABLES:
                self.assertEqual(target.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0], 0)


# Reuse the existing behavioral tests; omit only tests tied to direct SQLite
# connections or the SQLite-specific backup mock. No duplicated assertions.
SQLITE_ONLY = {'test_reset_operations_preserves_users_and_backup',
              'test_reset_backup_failure_preserves_data',
              'test_pending_payment_does_not_admit',
              'test_cash_totals_include_more_than_history_page',
              'test_backup_is_consistent_and_restorable'}
for name in dir(existing.AccessTests):
    if name.startswith('test_') and name not in SQLITE_ONLY:
        setattr(PostgresTests, name, getattr(existing.AccessTests, name))
