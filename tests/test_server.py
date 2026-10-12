import concurrent.futures
from contextlib import closing
import importlib.util
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest

from server import create_app, init_db

PASSWORD = 'test-password-2026'


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'tickets.db'
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        init_db(self.app)
        self.admin = self.app.test_client()
        response = self.admin.post('/api/auth/setup', json={'setupCode': self.app.config['SETUP_CODE'], 'username': 'admin', 'name': 'Admin', 'password': PASSWORD}, headers={'X-Requested-With': 'Acceso'})
        self.assertEqual(response.status_code, 200)
        self.csrf = self.login(self.admin)

    def tearDown(self):
        self.temp.cleanup()

    def login(self, client, username='admin'):
        r = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD}, headers={'X-Requested-With': 'Acceso'})
        self.assertEqual(r.status_code, 200, r.json)
        return r.json['csrf']

    def post(self, path, data, client=None, csrf=None, key=None, method='POST'):
        return (client or self.admin).open(path, method=method, json=data, headers={'X-Requested-With': 'Acceso', 'X-CSRF-Token': csrf or self.csrf, 'Idempotency-Key': key or secrets.token_hex(16)})

    def event(self, capacity=30, initial=0):
        r = self.post('/api/events', {'name': 'Evento prueba', 'date': '2026-10-10', 'time': '20:00', 'location': 'Local', 'capacity': capacity, 'initialQty': initial, 'tariffs': [{'name': 'General', 'price': 10000}]})
        self.assertEqual(r.status_code, 200, r.json)
        eid = r.json['event']['id']
        rate = next(t for t in self.admin.get('/api/bootstrap').json['tariffs'] if t['event_id'] == eid)
        return eid, rate['id']

    def sale(self, eid, rate, qty=1, **kwargs):
        return self.post('/api/sales/quick', {'eventId': eid, 'tariffId': rate, 'qty': qty, 'method': 'efectivo', 'admit': kwargs.pop('admit', False)}, **kwargs)

    def test_multiple_currencies_and_cash(self):
        for currency in ('PEN', 'USD', 'CLP', 'ARS', 'EUR', 'COP', 'MXN', 'BOB', 'BRL', 'UYU'):
            r = self.post('/api/events', {'name': currency, 'capacity': 5, 'initialQty': 0,
                'currency': currency, 'tariffs': [{'name': 'General', 'price': 12.50}]})
            self.assertEqual(r.status_code, 200, r.json)
            eid = r.json['event']['id']
            self.assertEqual(r.json['event']['currency'], currency)
            rate = next(t for t in self.admin.get('/api/bootstrap').json['tariffs'] if t['event_id'] == eid)
            sale = self.sale(eid, rate['id'])
            self.assertEqual(sale.status_code, 200, sale.json)
            self.assertEqual(sale.json['tickets'][0]['currency'], currency)
            self.assertEqual(sale.json['total_cents'], 1250)
            self.assertEqual(self.post('/api/events/'+eid, {'currency': 'USD' if currency != 'USD' else 'PEN'}, method='PATCH').status_code, 400)
        r = self.post('/api/cash', {'type': 'RETIRO', 'amount': 2.25, 'description': 'Cambio', 'method': 'efectivo', 'currency': 'PEN'})
        self.assertEqual(r.status_code, 200, r.json)
        totals = self.admin.get('/api/cash').json['totals']
        self.assertEqual(len(totals), 10)
        self.assertEqual(next(t for t in totals if t['currency']=='PEN')['withdrawals'], 225)
        self.assertTrue(all(t['income']==1250 for t in totals))
        for report in ('sales.csv', 'cash.csv'):
            self.assertIn('currency', self.admin.get('/api/reports/'+report).text)
        init_db(self.app)
        self.assertEqual(len(self.admin.get('/api/cash').json['totals']), 10)

    def test_invalid_currency_and_legacy_default(self):
        eid, _ = self.event()
        event = next(e for e in self.admin.get('/api/bootstrap').json['events'] if e['id']==eid)
        self.assertEqual(event['currency'], 'CLP')
        for value in ('XYZ', '', None, [], 'pen'):
            r = self.post('/api/events', {'name': 'Invalid', 'capacity': 5, 'currency': value, 'tariffs': [{'name':'General','price':10}]})
            self.assertEqual(r.status_code, 400)
            r = self.post('/api/cash', {'type':'INGRESO','amount':1,'description':'Test','method':'efectivo','currency':value})
            self.assertEqual(r.status_code, 400)


    def prepare_reset(self, mode='operations'):
        r = self.post('/api/system/reset/prepare', {'mode':mode, 'password':PASSWORD})
        self.assertEqual(r.status_code, 200, r.json)
        token = r.json['token']
        download = self.admin.get('/api/system/reset/backup/'+token)
        self.assertEqual(download.status_code, 200)
        download.close()
        return token

    def test_reset_operations_preserves_users_and_backup(self):
        eid, rate = self.event()
        self.sale(eid, rate)
        seller, csrf = self.worker('vendedor')
        token = self.prepare_reset()
        # A sale after the downloaded snapshot must exist in the final local backup.
        self.sale(eid, rate)
        r = self.post('/api/system/reset', {'token':token,'password':PASSWORD,'confirmation':'RESTABLECER'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertFalse(r.json['setupRequired'])
        self.assertEqual(seller.get('/api/bootstrap').status_code, 401)
        self.assertEqual(self.admin.get('/api/bootstrap').status_code, 401)
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM users').fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT count(*) FROM events').fetchone()[0], 0)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        with closing(sqlite3.connect(self.path.parent/'backups'/r.json['backup'])) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM payments').fetchone()[0], 2)
        self.csrf = self.login(self.admin)
        self.assertEqual(self.admin.get('/api/bootstrap').json['events'], [])

    def test_reset_full_and_new_admin(self):
        self.event()
        token = self.prepare_reset('full')
        r = self.post('/api/system/reset', {'token':token,'password':PASSWORD,'confirmation':'RESTABLECER'})
        self.assertEqual(r.status_code, 200, r.json)
        self.assertTrue(self.admin.get('/api/auth/status').json['setupRequired'])
        self.assertEqual(self.admin.get('/api/system/reset/backup/'+token).status_code, 401)
        r = self.admin.post('/api/auth/setup', json={'setupCode':r.json['setupCode'], 'username':'newadmin','name':'New','password':PASSWORD}, headers={'X-Requested-With':'Acceso'})
        self.assertEqual(r.status_code, 200, r.json)
        self.login(self.admin, 'newadmin')

    def test_reset_guards(self):
        self.event()
        seller, csrf = self.worker('vendedor')
        self.assertEqual(self.post('/api/system/reset/prepare', {'mode':'full','password':PASSWORD},seller,csrf).status_code,403)
        self.assertEqual(self.post('/api/system/reset/prepare', {'mode':'full','password':'wrong'}).status_code,403)
        r = self.post('/api/system/reset/prepare', {'mode':'full','password':PASSWORD})
        token = r.json['token']
        self.assertEqual(self.post('/api/system/reset', {'token':token,'password':PASSWORD,'confirmation':'RESTABLECER'}).status_code,409)
        response = self.admin.get('/api/system/reset/backup/'+token);response.close()
        self.assertEqual(self.post('/api/system/reset', {'token':token,'password':PASSWORD,'confirmation':'wrong'}).status_code,400)
        self.assertEqual(len(self.admin.get('/api/bootstrap').json['events']),1)

    def test_reset_backup_failure_preserves_data(self):
        from unittest.mock import patch
        self.event()
        token = self.prepare_reset('full')
        with patch('backup.backup_database', side_effect=OSError('Disk full')):
            r = self.post('/api/system/reset', {'token':token,'password':PASSWORD,'confirmation':'RESTABLECER'})
        self.assertEqual(r.status_code,503)
        self.assertEqual(len(self.admin.get('/api/bootstrap').json['events']),1)

    def worker(self, role):
        self.assertEqual(self.post('/api/users', {'username': role, 'name': role, 'password': PASSWORD, 'role': role}).status_code, 200)
        client = self.app.test_client()
        return client, self.login(client, role)

    def test_authentication_and_private_files(self):
        anonymous = self.app.test_client()
        for path in ('/api/bootstrap', '/api/cash', '/api/tickets', '/api/users', '/api/audit'):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        for path in ('/tickets.db', '/server.py', '/legacy/server-v7.py', '/static/../tickets.db', '/ticket-access-demo.html'):
            self.assertEqual(anonymous.get(path).status_code, 404, path)
        response = anonymous.get('/')
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_setup_cannot_be_repeated(self):
        r = self.post('/api/auth/setup', {'setupCode': self.app.config['SETUP_CODE'], 'username': 'evil', 'name': 'x', 'password': PASSWORD})
        self.assertEqual(r.status_code, 409)

    def test_csrf_and_roles(self):
        self.assertEqual(self.admin.post('/api/events', json={}).status_code, 403)
        validator, csrf = self.worker('validador')
        for path in ('/api/tickets', '/api/users', '/api/cash', '/api/audit'):
            self.assertEqual(validator.get(path).status_code, 403, path)
        self.assertEqual(self.post('/api/sales/quick', {}, validator, csrf).status_code, 403)
        self.assertNotIn('tickets', validator.get('/api/bootstrap').json)
        seller, csrf = self.worker('vendedor')
        self.assertEqual(self.post('/api/events', {}, seller, csrf).status_code, 403)
        self.assertEqual(seller.get('/api/cash').status_code, 403)

    def test_logout_revokes_session(self):
        self.assertEqual(self.post('/api/auth/logout', {}).status_code, 200)
        self.assertEqual(self.admin.get('/api/bootstrap').status_code, 401)

    def test_disable_user_revokes_session(self):
        seller, csrf = self.worker('vendedor')
        uid = next(u['id'] for u in self.admin.get('/api/users').json['users'] if u['role'] == 'vendedor')
        self.assertEqual(self.post('/api/users/' + uid, {'active': False}, method='PATCH').status_code, 200)
        self.assertEqual(seller.get('/api/bootstrap').status_code, 401)

    def test_bulk_sale_and_cash(self):
        eid, rate = self.event()
        r = self.sale(eid, rate, 3)
        self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual(r.json['total_cents'], 3000000)
        self.assertEqual(len({t['token'] for t in r.json['tickets']}), 3)
        self.assertTrue(all(t['status'] == 'VENDIDA' for t in r.json['tickets']))
        cash = self.admin.get('/api/cash').json
        self.assertEqual(cash['totals'][0]['income'], 3000000)
        self.assertEqual(len(cash['movements']), 3)

    def test_retry_sale_returns_same_tickets(self):
        eid, rate = self.event()
        key = secrets.token_hex(16)
        a, b = self.sale(eid, rate, 3, key=key), self.sale(eid, rate, 3, key=key)
        self.assertEqual(a.json, b.json)
        self.assertEqual(len(self.admin.get('/api/cash').json['movements']), 3)
        self.assertEqual(self.sale(eid, rate, 4, key=key).status_code, 409)

    def test_cannot_exceed_capacity_and_sale_rolls_back(self):
        eid, rate = self.event(2)
        self.assertEqual(self.sale(eid, rate, 3).status_code, 409)
        self.assertEqual(self.admin.get('/api/tickets?eventId=' + eid).json['total'], 0)
        self.assertEqual(self.admin.get('/api/cash').json['movements'], [])

    def test_concurrent_sales_do_not_oversell(self):
        eid, rate = self.event(5)
        clients = [self.app.test_client(), self.app.test_client()]
        tokens = [self.login(c) for c in clients]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda i: self.sale(eid, rate, 4, client=clients[i], csrf=tokens[i]).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.admin.get('/api/tickets?eventId=' + eid).json['total'], 4)

    def test_concurrent_validation_only_one_entry(self):
        eid, rate = self.event()
        t = self.sale(eid, rate).json['tickets'][0]
        clients = [self.app.test_client(), self.app.test_client()]
        tokens = [self.login(c) for c in clients]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda i: self.post('/api/validate', {'eventId': eid, 'token': t['token']}, clients[i], tokens[i]).json['result'], range(2)))
        self.assertEqual(sorted(results), ['USED', 'VALID'])

    def test_retry_validation_recovers_green_result(self):
        eid, rate = self.event()
        t = self.sale(eid, rate).json['tickets'][0]
        data, key = {'eventId': eid, 'token': t['token']}, secrets.token_hex(16)
        a, b = self.post('/api/validate', data, key=key), self.post('/api/validate', data, key=key)
        self.assertEqual(a.json, b.json)
        self.assertEqual(a.json['result'], 'VALID')
        self.assertEqual(self.post('/api/validate', data).json['result'], 'USED')

    def test_admit_at_sale(self):
        eid, rate = self.event()
        r = self.sale(eid, rate, 2, admit=True)
        self.assertTrue(all(t['status'] == 'USADA' for t in r.json['tickets']))
        t = r.json['tickets'][0]
        self.assertEqual(self.post('/api/validate', {'eventId': eid, 'token': t['token']}).json['result'], 'USED')

    def test_inactive_and_wrong_event(self):
        eid, rate = self.event()
        other, _ = self.event()
        t = self.sale(eid, rate).json['tickets'][0]
        r = self.post('/api/validate', {'eventId': other, 'token': t['token']})
        self.assertEqual(r.json['result'], 'INVALID')
        self.assertIsNone(r.json['ticketId'])
        self.post('/api/events/' + eid, {'status': 'inactivo'}, method='PATCH')
        self.assertEqual(self.post('/api/validate', {'eventId': eid, 'token': t['token']}).status_code, 409)
        self.assertEqual(self.sale(eid, rate).status_code, 409)

    def test_pending_payment_does_not_admit(self):
        eid, rate = self.event()
        t = self.sale(eid, rate).json['tickets'][0]
        with closing(sqlite3.connect(self.path, isolation_level=None)) as c:
            c.execute('UPDATE tickets SET paid_cents=0,paid_amount=0 WHERE id=?', (t['id'],))
        self.assertEqual(self.post('/api/validate', {'eventId': eid, 'token': t['token']}).json['result'], 'PAYMENT_PENDING')
        key = secrets.token_hex(16)
        a = self.post('/api/tickets/' + t['id'] + '/payment', {'amount': 10000}, key=key)
        b = self.post('/api/tickets/' + t['id'] + '/payment', {'amount': 10000}, key=key)
        self.assertEqual(a.json, b.json)
        self.assertEqual(a.json['ticket']['status'], 'VENDIDA')
        self.assertEqual(self.post('/api/validate', {'eventId': eid, 'token': t['token']}).json['result'], 'VALID')

    def test_cancel_and_audit_identity(self):
        eid, rate = self.event()
        t = self.sale(eid, rate).json['tickets'][0]
        self.assertEqual(self.post('/api/tickets/' + t['id'] + '/cancel', {'reason': 'Solicitud cliente'}).status_code, 200)
        r = self.post('/api/validate', {'eventId': eid, 'token': t['token'], 'userName': 'Impostor'})
        self.assertEqual(r.json['result'], 'CANCELLED')
        self.assertEqual(self.admin.get('/api/audit').json['validations'][0]['user_name'], 'Admin')

    def test_invalid_amounts_and_quantities(self):
        eid, rate = self.event()
        for qty in (-1, 0, 1.5, True, 'NaN', 101):
            self.assertEqual(self.sale(eid, rate, qty).status_code, 400, qty)
        for amount in (-1, 'NaN', 'Infinity', 0, .001):
            self.assertEqual(self.post('/api/cash', {'type': 'INGRESO', 'amount': amount, 'description': 'x'}).status_code, 400, amount)

    def test_generate_respects_separate_capacity(self):
        eid, rate = self.event(10, 3)
        self.assertEqual(self.post('/api/events/' + eid + '/tickets/generate', {'qty': 7}).status_code, 200)
        self.assertEqual(self.post('/api/events/' + eid + '/tickets/generate', {'qty': 1}).status_code, 409)
        self.assertEqual(self.sale(eid, rate, 10).status_code, 200)

    def test_cash_totals_include_more_than_history_page(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as c:
            c.executemany("INSERT INTO cash_movements(id,type,amount,amount_cents,method,date) VALUES(?,'INGRESO',1,100,'efectivo','2026-01-01')", [(str(i),) for i in range(1100)])
        data = self.admin.get('/api/cash').json
        self.assertEqual(len(data['movements']), 100)
        self.assertEqual(data['totals'][0]['income'], 110000)

    def test_queue_of_twenty(self):
        eid, rate = self.event(20)
        for i in range(20):
            t = self.sale(eid, rate).json['tickets'][0]
            self.assertEqual(self.post('/api/validate', {'eventId': eid, 'token': t['token']}).json['result'], 'VALID')
        stats = next(s for s in self.admin.get('/api/bootstrap').json['stats'] if s['event_id'] == eid)
        self.assertEqual(stats['used'], 20)
        self.assertEqual(self.sale(eid, rate).status_code, 409)

    def test_individual_sale_partial_and_payment_replay(self):
        eid, rate = self.event(5, 1)
        t = self.admin.get('/api/tickets?eventId='+eid).json['tickets'][0]
        result = self.post('/api/tickets/'+t['id']+'/sale', {'tariffId':rate, 'initialPayment':4000, 'customerName':'Cliente'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['ticket']['balance_cents'],600000)
        self.assertEqual(self.post('/api/validate', {'eventId':eid,'token':t['token']}).json['result'],'PAYMENT_PENDING')
        self.assertEqual(self.post('/api/tickets/'+t['id']+'/sale', {'tariffId':rate}).status_code,409)
        self.assertEqual(self.admin.get('/api/cash').json['totals'][0]['income'],400000)

    def test_reports_escape_spreadsheet_formulas(self):
        eid, rate = self.event(5, 1)
        t = self.admin.get('/api/tickets?eventId='+eid).json['tickets'][0]
        self.post('/api/tickets/'+t['id']+'/sale', {'tariffId':rate, 'customerName':'=1+1'})
        report = self.admin.get('/api/reports/sales.csv')
        self.assertEqual(report.status_code,200)
        self.assertIn("'=1+1", report.get_data(as_text=True))
        seller, csrf = self.worker('vendedor')
        self.assertEqual(seller.get('/api/reports/sales.csv').status_code,403)

    def test_backup_is_consistent_and_restorable(self):
        from backup import backup_database
        eid, rate = self.event()
        self.sale(eid,rate,3)
        target=Path(self.temp.name)/'copy.db'
        backup_database(self.path,target)
        with closing(sqlite3.connect(target)) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('SELECT count(*) FROM tickets').fetchone()[0],3)
        restored=create_app(target)
        init_db(restored)
        client=restored.test_client()
        self.login(client)
        self.assertEqual(client.get('/api/cash').json['totals'][0]['income'],3000000)


class MigrationTests(unittest.TestCase):
    def test_legacy_database_backup_and_cents(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = importlib.util.spec_from_file_location('legacy', Path(__file__).resolve().parents[1] / 'legacy/server-v7.py')
            legacy = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(legacy)
            path = Path(directory) / 'tickets.db'
            legacy.DB = str(path)
            legacy.init_db()
            with closing(sqlite3.connect(path, isolation_level=None)) as c:
                c.execute("INSERT INTO events(id,name,capacity,created_at) VALUES('e','Anterior',5,'2025-01-01')")
                c.execute("INSERT INTO tickets(id,event_id,token,status,price,paid_amount,created_at) VALUES('t','e','legacy-token','VENDIDA',123.45,23.40,'2025-01-01')")
            app = create_app(path)
            init_db(app)
            init_db(app)
            with closing(sqlite3.connect(path, isolation_level=None)) as c:
                self.assertEqual(c.execute('SELECT price_cents,paid_cents,token FROM tickets').fetchone(), (12345, 2340, 'legacy-token'))
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            backups = list((Path(directory) / 'backups').glob('*.db'))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as c:
                self.assertEqual(c.execute('SELECT price,paid_amount FROM tickets').fetchone(), (123.45, 23.4))
                self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main(verbosity=2)
