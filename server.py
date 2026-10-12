"""Acceso: ventas y validación con sesiones, roles y operaciones atómicas."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
import csv
import io
import hashlib
import json
import os
import secrets
import sqlite3
import time
import zipfile

import qrcode
from PIL import Image, ImageDraw, ImageFont

from flask import Flask, Response, g, jsonify, request, send_from_directory, send_file
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent
CURRENCIES = ('PEN', 'USD', 'CLP', 'ARS', 'EUR', 'COP', 'MXN', 'BOB', 'BRL', 'UYU')


def currency_field(data, default='CLP'):
    value = data.get('currency', default)
    if not isinstance(value, str) or value not in CURRENCIES:
        raise Problem('Selecciona una moneda válida')
    return value


ROLES = ('admin', 'vendedor', 'validador')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def ident(prefix):
    return prefix + '_' + secrets.token_hex(12)


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def text_field(data, key, limit=150, required=False):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit:
        raise Problem(f'Campo inválido: {key}')
    value = value.strip()
    if required and not value:
        raise Problem(f'Completa {key}')
    return value


def integer(value, low, high, label):
    if isinstance(value, bool):
        raise Problem(f'{label} debe ser un número entero')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value() or not low <= number <= high:
            raise ValueError()
        return int(number)
    except (ValueError, InvalidOperation):
        raise Problem(f'{label}: usa un entero entre {low} y {high}')


def cents(value, positive=False, whole=False):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n > 1000000000 or n * 100 != (n * 100).to_integral_value():
            raise ValueError()
        if positive and n <= 0 or whole and n != n.to_integral_value():
            raise ValueError()
        return int(n * 100)
    except (ValueError, InvalidOperation):
        raise Problem('Monto inválido. Usa un monto positivo con hasta dos decimales.')


def create_app(database=None):
    app = Flask(__name__, static_folder=str(BASE / 'static'), static_url_path='/static')
    app.config.update(DATABASE=str(database or BASE / 'tickets.db'), MAX_CONTENT_LENGTH=64 * 1024,
                      SETUP_CODE=os.environ.get('ACCESO_SETUP_CODE') or secrets.token_urlsafe(18))

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=15, isolation_level=None)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        if 'db' in g:
            g.pop('db').close()

    def audit(action, detail):
        db().execute('INSERT INTO audit VALUES(?,?,?,?,?)',
                     (ident('audit'), g.user['id'], action, json.dumps(detail, ensure_ascii=False), now()))

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise Problem('Envía un objeto JSON válido')
        return data

    def public_user(row):
        return {key: row[key] for key in ('id', 'username', 'name', 'role', 'active')}

    def event(row):
        return dict(row)

    def ticket(row):
        data = dict(row)
        data['ticket_number'] = data.get('ticket_number')
        data['currency'] = db().execute('SELECT currency FROM events WHERE id=?', (data['event_id'],)).fetchone()[0]
        data['balance_cents'] = max(0, data['price_cents'] - data['paid_cents'])
        return data

    def active_event(eid):
        row = db().execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
        if not row or row['status'] != 'activo':
            raise Problem('El evento no está activo', 409)
        return row

    def record_payment(tid, amount, method):
        if amount <= 0:
            return
        ts, pid = now(), ident('pay')
        db().execute('INSERT INTO payments(id,ticket_id,amount,user_name,date,amount_cents,method) VALUES(?,?,?,?,?,?,?)',
                     (pid, tid, amount / 100, g.user['name'], ts, amount, method))
        db().execute('INSERT INTO cash_movements(id,type,amount,description,user_name,date,amount_cents,method,ticket_id,currency) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (ident('cash'), 'INGRESO', amount / 100, 'Pago entrada ' + tid, g.user['name'], ts, amount, method, tid, db().execute('SELECT e.currency FROM tickets t JOIN events e ON e.id=t.event_id WHERE t.id=?', (tid,)).fetchone()[0]))

    def method_of(data):
        method = data.get('method', 'efectivo')
        if method not in ('efectivo', 'transferencia', 'tarjeta'):
            raise Problem('Método de pago inválido')
        return method

    def operation(roles, replay=False):
        def decorate(fn):
            @wraps(fn)
            def run(*args, **kwargs):
                if g.user['role'] not in roles:
                    raise Problem('No tienes permiso para esta operación', 403)
                if request.method == 'GET':
                    return fn(*args, **kwargs)
                data = body()
                connection = db()
                connection.execute('BEGIN IMMEDIATE')
                try:
                    current_session = connection.execute('SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>? AND u.active=1 AND u.role=?', (g.session['id'], time.time(), g.user['role'])).fetchone()
                    if not current_session:
                        raise Problem('La sesión terminó. Inicia sesión nuevamente.', 401)
                    key = request.headers.get('Idempotency-Key', '')
                    fingerprint = hashlib.sha256((request.path + json.dumps(data, sort_keys=True)).encode()).hexdigest()
                    if replay:
                        if not 16 <= len(key) <= 100:
                            raise Problem('Falta el identificador de la operación')
                        previous = connection.execute('SELECT * FROM operations WHERE user_id=? AND key=?', (g.user['id'], key)).fetchone()
                        if previous:
                            if previous['fingerprint'] != fingerprint:
                                raise Problem('Este identificador corresponde a otra operación', 409)
                            connection.execute('COMMIT')
                            return jsonify(json.loads(previous['response']))
                    result = fn(*args, **kwargs)
                    if replay:
                        connection.execute('INSERT INTO operations VALUES(?,?,?,?,?)',
                                           (g.user['id'], key, fingerprint, json.dumps(result), now()))
                    connection.execute('COMMIT')
                    return jsonify(result)
                except Exception:
                    if connection.in_transaction:
                        connection.execute('ROLLBACK')
                    raise
            return run
        return decorate

    @app.errorhandler(Problem)
    def problem(error):
        return jsonify(error=error.message), error.status

    @app.errorhandler(sqlite3.IntegrityError)
    def invalid_record(error):
        return jsonify(error='La operación entra en conflicto con un registro existente.'), 409

    @app.errorhandler(sqlite3.OperationalError)
    def busy(error):
        app.logger.exception('Database operation failed')
        return jsonify(error='La operación no pudo completarse. Reintenta con el mismo botón.'), 503

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.before_request
    def authenticate():
        g.user = None
        if not request.path.startswith('/api/'):
            return
        if request.method != 'GET' and request.headers.get('X-Requested-With') != 'Acceso':
            raise Problem('Solicitud no autorizada', 403)
        raw = request.cookies.get('acceso_session', '')
        digest = hashlib.sha256(raw.encode()).hexdigest()
        g.session = db().execute('SELECT s.*,u.username,u.name,u.role,u.active FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires>? AND u.active=1', (digest, time.time())).fetchone()
        if g.session:
            g.user = {'id': g.session['user_id'], **{k: g.session[k] for k in ('username', 'name', 'role', 'active')}}
        if request.path in ('/api/auth/status', '/api/auth/setup', '/api/auth/login'):
            return
        if not g.user:
            raise Problem('Inicia sesión para continuar', 401)
        if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), g.session['csrf']):
            raise Problem('Sesión desactualizada. Recarga la página.', 403)


    reset_preparations = {}

    def reset_password(data):
        row = db().execute('SELECT * FROM users WHERE id=? AND active=1', (g.user['id'],)).fetchone()
        if not row or row['role'] != 'admin' or not check_password_hash(row['password_hash'], text_field(data, 'password', 200, True)):
            raise Problem('Contraseña de administrador incorrecta', 403)

    def reset_backup():
        from backup import backup_database
        target = Path(app.config['DATABASE']).resolve().parent / 'backups' / ('before-reset-' + secrets.token_hex(16) + '.db')
        try:
            backup_database(app.config['DATABASE'], target)
        except Exception:
            app.logger.exception('Reset backup failed')
            raise Problem('No se pudo verificar el respaldo. No se borró ningún dato.', 503)
        return target

    def reset_preparation(token):
        item = reset_preparations.get(token)
        if not item or item['user'] != g.user['id'] or item['expires'] < time.time():
            raise Problem('Prepara un nuevo respaldo; la confirmación expiró.', 409)
        return item

    @app.post('/api/system/reset/prepare')
    @operation(('admin',))
    def prepare_reset():
        data = body()
        reset_password(data)
        mode = data.get('mode')
        if mode not in ('operations', 'full'):
            raise Problem('Selecciona el tipo de restablecimiento')
        path = reset_backup()
        token = secrets.token_urlsafe(32)
        for old in list(reset_preparations):
            if reset_preparations[old]['expires'] < time.time():
                del reset_preparations[old]
        reset_preparations[token] = dict(user=g.user['id'], mode=mode, path=path, expires=time.time()+900, downloaded=False)
        return {'token': token, 'filename': path.name}

    @app.get('/api/system/reset/backup/<token>')
    @operation(('admin',))
    def download_reset_backup(token):
        item = reset_preparation(token)
        response = send_file(item['path'], as_attachment=True, download_name=item['path'].name)
        item['downloaded'] = True
        return response

    @app.post('/api/system/reset')
    @operation(('admin',))
    def reset_system():
        data = body()
        reset_password(data)
        item = reset_preparation(text_field(data, 'token', 100, True))
        if data.get('confirmation') != 'RESTABLECER':
            raise Problem('Escribe RESTABLECER para confirmar')
        if not item['downloaded']:
            raise Problem('Descarga el respaldo antes de continuar', 409)
        # BEGIN IMMEDIATE prevents sales between this final backup and deletion.
        final_backup = reset_backup()
        for table in ('sessions', 'operations', 'validations', 'payments', 'cash_movements', 'tickets', 'ticket_batches', 'tariffs', 'events', 'audit', 'login_attempts'):
            db().execute('DELETE FROM ' + table)
        full = item['mode'] == 'full'
        if full:
            db().execute('DELETE FROM users')
        else:
            audit('RESET_OPERATIONS', {'backup': final_backup.name})
        reset_preparations.clear()
        if full:
            print('Código de instalación después del restablecimiento: ' + app.config['SETUP_CODE'], flush=True)
        return {'ok': True, 'setupRequired': full, 'setupCode': app.config['SETUP_CODE'] if full else None, 'backup': final_backup.name}


    @app.get('/')
    def index():
        return send_from_directory(BASE / 'static', 'index.html')

    @app.get('/api/auth/status')
    def status():
        return jsonify(setupRequired=not db().execute('SELECT 1 FROM users LIMIT 1').fetchone(),
                       user=g.user, csrf=g.session['csrf'] if g.session else None)

    def credentials(data):
        username = text_field(data, 'username', 60, True).lower()
        password = text_field(data, 'password', 200, True)
        if len(password) < 10:
            raise Problem('La contraseña debe tener al menos 10 caracteres')
        return username, password

    @app.post('/api/auth/setup')
    def setup():
        data = body()
        if not secrets.compare_digest(str(data.get('setupCode', '')), app.config['SETUP_CODE']):
            raise Problem('Código de instalación incorrecto. Revisa la consola del servidor.', 403)
        username, password = credentials(data)
        name = text_field(data, 'name', 100, True)
        c = db()
        c.execute('BEGIN IMMEDIATE')
        try:
            if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise Problem('El administrador ya fue creado', 409)
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,1)', (ident('user'), username, name, generate_password_hash(password), 'admin'))
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
        return jsonify(ok=True)

    @app.post('/api/auth/login')
    def login():
        data = body()
        username = text_field(data, 'username', 60, True).lower()
        password = text_field(data, 'password', 200, True)
        c = db()
        stamp = time.time()
        # Persistent throttle shared by all workers; old attempts expire after 15 minutes.
        c.execute('DELETE FROM login_attempts WHERE date<?', (stamp - 900,))
        failures = c.execute('SELECT count(*) FROM login_attempts WHERE username=? OR address=?', (username, request.remote_addr)).fetchone()[0]
        if failures >= 10:
            raise Problem('Demasiados intentos. Espera 15 minutos.', 429)
        user = c.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
        if not user or not check_password_hash(user['password_hash'], password):
            c.execute('INSERT INTO login_attempts VALUES(?,?,?)', (username, request.remote_addr, stamp))
            raise Problem('Usuario o contraseña incorrectos', 401)
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        c.execute('DELETE FROM sessions WHERE expires<?', (stamp,))
        if g.session:
            c.execute('DELETE FROM sessions WHERE id=?', (g.session['id'],))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(raw.encode()).hexdigest(), user['id'], csrf, stamp + 12 * 3600))
        response = jsonify(user=public_user(user), csrf=csrf)
        response.set_cookie('acceso_session', raw, httponly=True, secure=request.is_secure or os.environ.get('ACCESO_HTTPS') == '1', samesite='Strict', max_age=12 * 3600)
        return response

    @app.post('/api/auth/logout')
    def logout():
        db().execute('DELETE FROM sessions WHERE id=?', (g.session['id'],))
        response = jsonify(ok=True)
        response.delete_cookie('acceso_session')
        return response

    @app.get('/api/bootstrap')
    def bootstrap():
        events = [event(r) for r in db().execute("SELECT * FROM events WHERE status!='archivado' ORDER BY created_at DESC")]
        tariffs = [dict(r) for r in db().execute('SELECT * FROM tariffs ORDER BY price_cents')]
        stats = [dict(r) for r in db().execute("SELECT event_id,count(*) issued,sum(status='DISPONIBLE') available,sum(status='VENDIDA') sold,sum(status='USADA') used FROM tickets GROUP BY event_id")]
        return jsonify(events=events, tariffs=tariffs, stats=stats, user=g.user)

    @app.get('/api/users')
    @operation(('admin',))
    def users():
        return jsonify(users=[public_user(r) for r in db().execute('SELECT * FROM users ORDER BY name')])

    @app.post('/api/users')
    @operation(('admin',))
    def add_user():
        data = body()
        username, password = credentials(data)
        role = data.get('role')
        if role not in ROLES:
            raise Problem('Rol inválido')
        if db().execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            raise Problem('Ese usuario ya existe', 409)
        uid = ident('user')
        db().execute('INSERT INTO users VALUES(?,?,?,?,?,1)', (uid, username, text_field(data, 'name', 100, True), generate_password_hash(password), role))
        audit('CREATE_USER', {'id': uid, 'role': role})
        return {'ok': True}

    @app.patch('/api/users/<uid>')
    @operation(('admin',))
    def update_user(uid):
        data = body()
        if uid == g.user['id']:
            raise Problem('No puedes desactivar tu propia cuenta')
        if not db().execute('SELECT 1 FROM users WHERE id=?', (uid,)).fetchone():
            raise Problem('Usuario no encontrado', 404)
        active = data.get('active')
        if not isinstance(active, bool):
            raise Problem('Estado inválido')
        db().execute('UPDATE users SET active=? WHERE id=?', (int(active), uid))
        db().execute('DELETE FROM sessions WHERE user_id=?', (uid,))
        audit('USER_STATUS', {'id': uid, 'active': active})
        return {'ok': True}

    @app.post('/api/events')
    @operation(('admin',))
    def create_event():
        data = body()
        capacity = integer(data.get('capacity'), 1, 100000, 'Aforo')
        initial = integer(data.get('initialQty', capacity), 0, capacity, 'Emisión inicial')
        currency = currency_field(data)
        eid, ts = ident('ev'), now()
        db().execute('INSERT INTO events(id,name,date,time,location,capacity,price,status,created_at,currency) VALUES(?,?,?,?,?,?,0,?,?,?)',
                     (eid, text_field(data, 'name', required=True), text_field(data, 'date', 20), text_field(data, 'time', 20), text_field(data, 'location'), capacity, 'activo', ts, currency))
        for _ in range(initial):
            issue(eid)
        tariffs = data.get('tariffs', [])
        if not isinstance(tariffs, list) or not 1 <= len(tariffs) <= 20:
            raise Problem('Agrega entre 1 y 20 tarifas')
        for tariff in tariffs:
            if not isinstance(tariff, dict):
                raise Problem('Tarifa inválida')
            db().execute('INSERT INTO tariffs VALUES(?,?,?,?)', (ident('rate'), eid, text_field(tariff, 'name', 60, True), cents(tariff.get('price'), True)))
        audit('CREATE_EVENT', {'id': eid, 'capacity': capacity, 'issued': initial})
        return {'event': event(db().execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone())}

    def next_ticket_number(eid):
        row = db().execute('SELECT COALESCE(MAX(ticket_number),0)+1 AS n FROM tickets WHERE event_id=?', (eid,)).fetchone()
        return int(row['n'])

    def issue(eid, tariff_id=None, batch_id=None):
        tid, number = ident('tk'), next_ticket_number(eid)
        db().execute("INSERT INTO tickets(id,event_id,token,status,created_at,ticket_number,tariff_id,batch_id) VALUES(?,?,?,'DISPONIBLE',?,?,?,?)", (tid, eid, secrets.token_hex(24), now(), number, tariff_id, batch_id))
        return tid

    @app.patch('/api/events/<eid>')
    @operation(('admin',))
    def edit_event(eid):
        data = body()
        row = db().execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
        if not row:
            raise Problem('Evento no encontrado', 404)
        if currency_field(data, row['currency']) != row['currency']:
            raise Problem('La moneda del evento no se puede cambiar. Crea otro evento para usar otra moneda.')
        status = data.get('status', row['status'])
        if status not in ('activo', 'inactivo', 'archivado'):
            raise Problem('Estado inválido')
        capacity = integer(data.get('capacity', row['capacity']), 1, 100000, 'Aforo')
        issued = db().execute('SELECT count(*) FROM tickets WHERE event_id=?', (eid,)).fetchone()[0]
        if capacity < issued:
            raise Problem('El aforo no puede ser menor que las entradas emitidas')
        fields = {k: text_field(data, k, 150, k == 'name') if k in data else row[k] for k in ('name', 'date', 'time', 'location')}
        db().execute('UPDATE events SET name=?,date=?,time=?,location=?,status=?,capacity=?,archived_at=? WHERE id=?', (fields['name'], fields['date'], fields['time'], fields['location'], status, capacity, now() if status == 'archivado' else None, eid))
        audit('EDIT_EVENT', {'id': eid, 'status': status, 'capacity': capacity})
        return {'ok': True}

    @app.post('/api/events/<eid>/tariffs')
    @operation(('admin',))
    def add_tariff(eid):
        active_event(eid)
        data = body()
        db().execute('INSERT INTO tariffs VALUES(?,?,?,?)', (ident('rate'), eid, text_field(data, 'name', 60, True), cents(data.get('price'), True)))
        audit('ADD_TARIFF', {'event': eid})
        return {'ok': True}

    @app.post('/api/events/<eid>/tickets/generate')
    @operation(('admin',), replay=True)
    def generate(eid):
        ev = active_event(eid)
        qty = integer(body().get('qty'), 1, 10000, 'Cantidad')
        issued = db().execute('SELECT count(*) FROM tickets WHERE event_id=?', (eid,)).fetchone()[0]
        if issued + qty > ev['capacity']:
            raise Problem('La emisión supera el aforo', 409)
        for _ in range(qty):
            issue(eid)
        audit('ISSUE', {'event': eid, 'quantity': qty})
        return {'ok': True}

    @app.post('/api/ticket-batches')
    @operation(('admin',), replay=True)
    def create_batch():
        data = body()
        eid = text_field(data, 'eventId', 80, True)
        ev = active_event(eid)
        tariff_id = text_field(data, 'tariffId', 80, True)
        tariff = db().execute('SELECT * FROM tariffs WHERE id=? AND event_id=?', (tariff_id, eid)).fetchone()
        if not tariff:
            raise Problem('Selecciona una tarifa del evento')
        qty = integer(data.get('qty'), 1, 10000, 'Cantidad')
        issued = db().execute('SELECT count(*) FROM tickets WHERE event_id=?', (eid,)).fetchone()[0]
        if issued + qty > ev['capacity']:
            raise Problem('La emisión del lote supera el aforo', 409)
        batch_id = ident('batch')
        label = text_field(data, 'label', 100) or ('Lote ' + datetime.now().strftime('%Y-%m-%d %H:%M'))
        start = next_ticket_number(eid)
        db().execute('INSERT INTO ticket_batches(id,event_id,tariff_id,label,quantity,start_number,end_number,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)',
                     (batch_id, eid, tariff_id, label, qty, start, start + qty - 1, now(), g.user['id']))
        ids = [issue(eid, tariff_id, batch_id) for _ in range(qty)]
        audit('CREATE_BATCH', {'batch': batch_id, 'event': eid, 'tariff': tariff_id, 'quantity': qty, 'start': start, 'end': start+qty-1})
        return {'batch': dict(db().execute('SELECT * FROM ticket_batches WHERE id=?', (batch_id,)).fetchone()), 'tickets': [ticket(db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()) for tid in ids]}

    @app.get('/api/ticket-batches')
    @operation(('admin',))
    def ticket_batches():
        eid = request.args.get('eventId','')
        rows = db().execute('SELECT b.*,e.name AS event_name,t.name AS tariff_name,t.price_cents FROM ticket_batches b JOIN events e ON e.id=b.event_id JOIN tariffs t ON t.id=b.tariff_id WHERE b.event_id=? ORDER BY b.created_at DESC', (eid,)).fetchall()
        return jsonify(batches=[dict(r) for r in rows])

    def batch_image(ticket_row, event_row, tariff_row):
        scale = 2
        img = Image.new('RGB', (700, 900), 'white')
        draw = ImageDraw.Draw(img)
        bold = ImageFont.truetype('arialbd.ttf', 34) if Path('C:/Windows/Fonts/arialbd.ttf').exists() else ImageFont.load_default()
        normal = ImageFont.truetype('arial.ttf', 24) if Path('C:/Windows/Fonts/arial.ttf').exists() else ImageFont.load_default()
        draw.text((350,45), 'ENTRIX', fill='#111827', font=bold, anchor='ma')
        draw.text((350,95), str(event_row['name'])[:45], fill='#111827', font=normal, anchor='ma')
        qr = qrcode.make(ticket_row['token']).convert('RGB').resize((420,420))
        img.paste(qr, (140,150))
        draw.text((350,610), f"ENTRADA N.º {int(ticket_row['ticket_number']):04d}", fill='#111827', font=bold, anchor='ma')
        draw.text((350,665), f"{event_row['date']} · {event_row['time']}", fill='#374151', font=normal, anchor='ma')
        draw.text((350,710), str(event_row['location'])[:55], fill='#374151', font=normal, anchor='ma')
        draw.text((350,755), f"{tariff_row['name']} · {event_row['currency']}", fill='#111827', font=normal, anchor='ma')
        price = float(tariff_row.get('price_cents', 0)) / 100
        draw.text((350,805), f"PRECIO: {event_row['currency']} {price:,.2f}", fill='#111827', font=bold, anchor='ma')
        draw.text((350,855), 'Presenta este QR para ingresar', fill='#374151', font=normal, anchor='ma')
        out=io.BytesIO(); img.save(out, format='JPEG', quality=94, optimize=True); out.seek(0); return out.getvalue()

    @app.get('/api/ticket-batches/<bid>/export.zip')
    @operation(('admin',))
    def export_batch(bid):
        batch = db().execute('SELECT b.*,e.name AS event_name,e.date,e.time,e.location,e.currency,t.name AS tariff_name,t.price_cents FROM ticket_batches b JOIN events e ON e.id=b.event_id JOIN tariffs t ON t.id=b.tariff_id WHERE b.id=?', (bid,)).fetchone()
        if not batch:
            raise Problem('Lote no encontrado', 404)
        tickets = db().execute('SELECT * FROM tickets WHERE batch_id=? ORDER BY ticket_number', (bid,)).fetchall()
        event_row = {'name':batch['event_name'],'date':batch['date'],'time':batch['time'],'location':batch['location'],'currency':batch['currency']}
        tariff_row = {'name':batch['tariff_name'],'price_cents':batch['price_cents']}
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as z:
            for tr in tickets:
                content=batch_image(tr,event_row,tariff_row)
                z.writestr(f"ENTRIX-{int(tr['ticket_number']):04d}.jpg", content)
        data.seek(0)
        audit('EXPORT_BATCH', {'batch':bid,'quantity':len(tickets)})
        return send_file(data, mimetype='application/zip', as_attachment=True, download_name=f"ENTRIX-Lote-{bid[-8:]}.zip")

    @app.get('/api/tickets/<tid>/image.jpg')
    @operation(('admin','vendedor'))
    def ticket_image(tid):
        tr = db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
        if not tr:
            raise Problem('Entrada no encontrada', 404)
        ev = db().execute('SELECT * FROM events WHERE id=?', (tr['event_id'],)).fetchone()
        tariff = db().execute('SELECT * FROM tariffs WHERE id=?', (tr['tariff_id'],)).fetchone() if tr['tariff_id'] else None
        if not tariff:
            tariff = {'name':'Entrada', 'price_cents':tr['price_cents']}
        content = batch_image(tr, ev, tariff)
        return send_file(io.BytesIO(content), mimetype='image/jpeg', as_attachment=True, download_name=f"ENTRIX-{int(tr['ticket_number']):04d}.jpg")

    @app.get('/api/tickets/lookup')
    @operation(('admin','vendedor'))
    def ticket_lookup():
        eid = text_field({'eventId':request.args.get('eventId','')}, 'eventId', 80, True)
        q = request.args.get('q','').strip()[:40]
        if not q:
            raise Problem('Escribe un número de entrada')
        rows = db().execute('SELECT * FROM tickets WHERE event_id=? AND (CAST(ticket_number AS TEXT)=? OR instr(lower(customer_name),lower(?))>0) ORDER BY ticket_number LIMIT 20', (eid,q,q)).fetchall()
        return jsonify(tickets=[ticket(r) for r in rows])

    @app.post('/api/sales/quick')
    @operation(('admin', 'vendedor'), replay=True)
    def quick_sale():
        data = body()
        eid = text_field(data, 'eventId', 80, True)
        ev = active_event(eid)
        qty = integer(data.get('qty'), 1, 100, 'Cantidad')
        tariff = db().execute('SELECT * FROM tariffs WHERE id=? AND event_id=?', (text_field(data, 'tariffId', 80, True), eid)).fetchone()
        if not tariff:
            raise Problem('Selecciona una tarifa del evento')
        admit = data.get('admit', False)
        if not isinstance(admit, bool):
            raise Problem('Modo de ingreso inválido')
        method = method_of(data)
        name, phone = text_field(data, 'customerName', 100), text_field(data, 'customerPhone', 40)
        rows = db().execute("SELECT id FROM tickets WHERE event_id=? AND status='DISPONIBLE' AND (tariff_id IS NULL OR tariff_id=?) ORDER BY created_at,id LIMIT ?", (eid, tariff['id'], qty)).fetchall()
        ids = [r['id'] for r in rows]
        issued = db().execute('SELECT count(*) FROM tickets WHERE event_id=?', (eid,)).fetchone()[0]
        missing = qty - len(ids)
        if issued + missing > ev['capacity']:
            raise Problem('No hay suficientes entradas disponibles para esta venta', 409)
        ids.extend(issue(eid) for _ in range(missing))
        ts, price = now(), tariff['price_cents']
        for tid in ids:
            db().execute('UPDATE tickets SET status=?,price=?,paid_amount=?,price_cents=?,paid_cents=?,customer_name=?,customer_phone=?,used_at=?,used_by=? WHERE id=?',
                         ('USADA' if admit else 'VENDIDA', price / 100, price / 100, price, price, name, phone, ts if admit else None, g.user['name'] if admit else None, tid))
            record_payment(tid, price, method)
            if admit:
                db().execute('INSERT INTO validations VALUES(?,?,?,?,?,?)', (ident('val'), tid, eid, g.user['name'], 'VALID', ts))
        audit('QUICK_SALE', {'tickets': ids, 'admit': admit, 'method': method, 'total_cents': price * qty})
        return {'tickets': [ticket(db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()) for tid in ids], 'total_cents': price * qty, 'admitted': admit}

    @app.get('/api/tickets')
    @operation(('admin', 'vendedor'))
    def tickets():
        eid, query = request.args.get('eventId', ''), request.args.get('q', '')[:100]
        page = integer(request.args.get('page', 1), 1, 100000, 'Página')
        where, args = 'event_id=?', [eid]
        if query:
            where += ' AND (CAST(ticket_number AS TEXT)=? OR instr(lower(customer_name),lower(?))>0 OR instr(id,?)>0 OR instr(customer_phone,?)>0)'
            args += [query, query, query, query]
        total = db().execute('SELECT count(*) FROM tickets WHERE ' + where, args).fetchone()[0]
        rows = db().execute('SELECT * FROM tickets WHERE ' + where + ' ORDER BY created_at DESC,id LIMIT 50 OFFSET ?', args + [(page - 1) * 50])
        return jsonify(tickets=[ticket(r) for r in rows], total=total, page=page)

    @app.post('/api/tickets/<tid>/sale')
    @operation(('admin', 'vendedor'), replay=True)
    def individual_sale(tid):
        data = body()
        t = db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
        if not t or t['status'] != 'DISPONIBLE':
            raise Problem('La entrada ya no está disponible', 409)
        active_event(t['event_id'])
        requested_tariff = text_field(data, 'tariffId', 80)
        if requested_tariff:
            tariff = db().execute('SELECT * FROM tariffs WHERE id=? AND event_id=?', (requested_tariff, t['event_id'])).fetchone()
        elif t['tariff_id']:
            tariff = db().execute('SELECT * FROM tariffs WHERE id=? AND event_id=?', (t['tariff_id'], t['event_id'])).fetchone()
        else:
            tariff = None
        if not tariff:
            raise Problem('Selecciona una tarifa para esta entrada')
        amount, price = cents(data.get('initialPayment', 0)), tariff['price_cents']
        if amount > price:
            raise Problem('El pago inicial supera la tarifa')
        name, phone = text_field(data, 'customerName', 100), text_field(data, 'customerPhone', 40)
        db().execute("UPDATE tickets SET status='VENDIDA',price=?,paid_amount=?,price_cents=?,paid_cents=?,customer_name=?,customer_phone=? WHERE id=?", (price/100, amount/100, price, amount, name, phone, tid))
        record_payment(tid, amount, method_of(data))
        audit('INDIVIDUAL_SALE', {'ticket': tid, 'price_cents': price, 'paid_cents': amount})
        return {'ticket': ticket(db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone())}

    @app.post('/api/tickets/<tid>/payment')
    @operation(('admin', 'vendedor'), replay=True)
    def payment(tid):
        data = body()
        t = db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
        if not t:
            raise Problem('Entrada no encontrada', 404)
        active_event(t['event_id'])
        amount = cents(data.get('amount'), True)
        if t['status'] != 'VENDIDA' or amount > t['price_cents'] - t['paid_cents']:
            raise Problem('La entrada no admite este abono', 409)
        paid = t['paid_cents'] + amount
        db().execute('UPDATE tickets SET paid_cents=?,paid_amount=? WHERE id=?', (paid, paid / 100, tid))
        record_payment(tid, amount, method_of(data))
        audit('PAYMENT', {'ticket': tid, 'amount_cents': amount})
        return {'ticket': ticket(db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone())}

    @app.post('/api/tickets/<tid>/cancel')
    @operation(('admin',), replay=True)
    def cancel(tid):
        t = db().execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
        if not t or t['status'] in ('USADA', 'CANCELADA'):
            raise Problem('No se puede cancelar esta entrada', 409)
        reason = text_field(body(), 'reason', 300, True)
        db().execute("UPDATE tickets SET status='CANCELADA' WHERE id=?", (tid,))
        audit('CANCEL', {'ticket': tid, 'reason': reason, 'paid_cents': t['paid_cents']})
        return {'ok': True, 'message': 'Entrada cancelada. No se ha registrado una devolución.'}

    @app.post('/api/validate')
    @operation(ROLES, replay=True)
    def validate():
        data = body()
        eid = text_field(data, 'eventId', 80, True)
        active_event(eid)
        tok = text_field(data, 'token', 200, True)
        door = text_field(data, 'door', 60)
        t = db().execute('SELECT * FROM tickets WHERE token=?', (tok,)).fetchone()
        result, detail = 'INVALID', 'Entrada no encontrada o de otro evento'
        ts = now()
        if t and t['event_id'] == eid:
            result = {'USADA': 'USED', 'CANCELADA': 'CANCELLED', 'DISPONIBLE': 'NOT_SOLD'}.get(t['status'], 'INVALID')
            if t['status'] == 'VENDIDA':
                if t['paid_cents'] < t['price_cents']:
                    result = 'PAYMENT_PENDING'
                else:
                    db().execute("UPDATE tickets SET status='USADA',used_at=?,used_by=? WHERE id=? AND status='VENDIDA'", (ts, g.user['name'], t['id']))
                    result = 'VALID'
            detail = {'VALID': 'Puede pasar', 'USED': 'Ya ingresó: ' + str(t['used_at']), 'CANCELLED': 'Entrada cancelada', 'NOT_SOLD': 'Dirigir a caja', 'PAYMENT_PENDING': 'Dirigir a caja para completar el pago'}.get(result, detail)
        belongs = t is not None and t['event_id'] == eid
        db().execute('INSERT INTO validations VALUES(?,?,?,?,?,?)', (ident('val'), t['id'] if belongs else None, eid, g.user['name'], result, ts))
        audit('SCAN', {'ticket': t['id'] if belongs else None, 'event': eid, 'door': door, 'result': result})
        return {'result': result, 'detail': detail, 'ticketId': t['id'] if belongs else None, 'balance_cents': max(0, t['price_cents'] - t['paid_cents']) if belongs else None}

    @app.get('/api/cash')
    @operation(('admin',))
    def cash():
        totals = [dict(r) for r in db().execute("SELECT currency,method,sum(CASE WHEN type='INGRESO' THEN amount_cents ELSE 0 END) income,sum(CASE WHEN type='RETIRO' THEN amount_cents ELSE 0 END) withdrawals FROM cash_movements GROUP BY currency,method")]
        rows = [dict(r) for r in db().execute('SELECT * FROM cash_movements ORDER BY date DESC LIMIT 100')]
        return jsonify(totals=totals, movements=rows)

    @app.post('/api/cash')
    @operation(('admin',), replay=True)
    def cash_add():
        data = body()
        typ = data.get('type')
        if typ not in ('INGRESO', 'RETIRO'):
            raise Problem('Tipo inválido')
        amount = cents(data.get('amount'), True)
        desc = text_field(data, 'description', 300, True)
        method = method_of(data)
        db().execute('INSERT INTO cash_movements(id,type,amount,description,user_name,date,amount_cents,method,currency) VALUES(?,?,?,?,?,?,?,?,?)', (ident('cash'), typ, amount / 100, desc, g.user['name'], now(), amount, method, currency_field(data)))
        audit('CASH', {'type': typ, 'amount_cents': amount, 'description': desc})
        return {'ok': True}

    @app.get('/api/reports/<kind>')
    @operation(('admin',))
    def export_report(kind):
        if kind == 'sales.csv':
            rows = db().execute("SELECT t.id,e.name AS event,e.currency,t.status,t.customer_name,t.customer_phone,t.price_cents,t.paid_cents,t.used_at,t.used_by,t.created_at FROM tickets t JOIN events e ON e.id=t.event_id WHERE t.status!='DISPONIBLE' ORDER BY t.created_at")
        elif kind == 'cash.csv':
            rows = db().execute('SELECT id,type,currency,amount_cents,method,description,user_name,date FROM cash_movements ORDER BY date')
        else:
            raise Problem('Reporte no encontrado', 404)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow([col[0] for col in rows.description])
        for row in rows:
            writer.writerow(["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value for value in row])
        return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=' + kind})

    @app.get('/api/audit')
    @operation(('admin',))
    def audit_log():
        return jsonify(audit=[dict(r) for r in db().execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY date DESC LIMIT 200')],
                       validations=[dict(r) for r in db().execute('SELECT * FROM validations ORDER BY date DESC LIMIT 200')])

    return app


def init_db(app):
    path = Path(app.config['DATABASE'])
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None)
    try:
        version = c.execute('PRAGMA user_version').fetchone()[0]
        existing = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tickets'").fetchone()
        if existing and version < 9:
            backup_dir = path.parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            backup = sqlite3.connect(backup_dir / ('tickets-before-v9-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db'))
            try:
                c.backup(backup)
            finally:
                backup.close()
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,name TEXT NOT NULL,date TEXT,time TEXT,location TEXT,capacity INTEGER NOT NULL DEFAULT 0,price REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'activo',created_at TEXT NOT NULL,archived_at TEXT);
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES events(id),token TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'DISPONIBLE',price REAL NOT NULL DEFAULT 0,customer_name TEXT,customer_phone TEXT,paid_amount REAL NOT NULL DEFAULT 0,used_at TEXT,used_by TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS validations(id TEXT PRIMARY KEY,ticket_id TEXT,event_id TEXT NOT NULL,user_name TEXT NOT NULL,result TEXT NOT NULL,date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,ticket_id TEXT NOT NULL REFERENCES tickets(id),amount REAL NOT NULL,user_name TEXT,date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cash_movements(id TEXT PRIMARY KEY,type TEXT NOT NULL CHECK(type IN ('INGRESO','RETIRO')),amount REAL NOT NULL,description TEXT,user_name TEXT,date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','vendedor','validador')),active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tariffs(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES events(id),name TEXT NOT NULL,price_cents INTEGER NOT NULL CHECK(price_cents>0));
        CREATE TABLE IF NOT EXISTS ticket_batches(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES events(id),tariff_id TEXT NOT NULL REFERENCES tariffs(id),label TEXT NOT NULL,quantity INTEGER NOT NULL,start_number INTEGER NOT NULL,end_number INTEGER NOT NULL,created_at TEXT NOT NULL,created_by TEXT);
        CREATE TABLE IF NOT EXISTS operations(user_id TEXT NOT NULL,key TEXT NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,date TEXT NOT NULL,PRIMARY KEY(user_id,key));
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL,date TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(username TEXT,address TEXT,date REAL);
        CREATE INDEX IF NOT EXISTS idx_ticket_event_status ON tickets(event_id,status);
        CREATE INDEX IF NOT EXISTS idx_validation_date ON validations(date);
        CREATE INDEX IF NOT EXISTS idx_cash_date ON cash_movements(date);
        CREATE INDEX IF NOT EXISTS idx_audit_date ON audit(date);
        ''')
        additions = {'events': [('archived_at', 'TEXT'), ('currency', "TEXT NOT NULL DEFAULT 'CLP'")],
                     'tickets': [('price_cents', 'INTEGER NOT NULL DEFAULT 0'), ('paid_cents', 'INTEGER NOT NULL DEFAULT 0'), ('ticket_number', 'INTEGER'), ('tariff_id', 'TEXT'), ('batch_id', 'TEXT')],
                     'payments': [('amount_cents', 'INTEGER NOT NULL DEFAULT 0'), ('method', "TEXT NOT NULL DEFAULT 'sin_clasificar'")],
                     'cash_movements': [('amount_cents', 'INTEGER NOT NULL DEFAULT 0'), ('method', "TEXT NOT NULL DEFAULT 'sin_clasificar'"), ('ticket_id', 'TEXT'), ('currency', "TEXT NOT NULL DEFAULT 'CLP'")]}
        for table, columns in additions.items():
            present = {r[1] for r in c.execute('PRAGMA table_info(' + table + ')')}
            for name, definition in columns:
                if name not in present:
                    c.execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + definition)
        # Assign stable human-readable ticket numbers to legacy entries, per event.
        for er in c.execute('SELECT id FROM events').fetchall():
            eid = er[0]
            rows = c.execute('SELECT id FROM tickets WHERE event_id=? AND (ticket_number IS NULL OR ticket_number=0) ORDER BY created_at,id', (eid,)).fetchall()
            start = c.execute('SELECT COALESCE(MAX(ticket_number),0) FROM tickets WHERE event_id=?', (eid,)).fetchone()[0]
            for idx, row in enumerate(rows, start=1):
                c.execute('UPDATE tickets SET ticket_number=? WHERE id=?', (start+idx, row[0]))
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_ticket_event_number ON tickets(event_id,ticket_number)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ticket_batch ON tickets(batch_id)')

        if version < 8:
            c.execute('UPDATE tickets SET price_cents=CAST(round(price*100) AS INTEGER),paid_cents=CAST(round(paid_amount*100) AS INTEGER)')
            for table in ('payments', 'cash_movements'):
                c.execute('UPDATE ' + table + ' SET amount_cents=CAST(round(amount*100) AS INTEGER)')
            c.execute('PRAGMA user_version=9')
        c.execute('COMMIT')
    except Exception:
        if c.in_transaction:
            c.execute('ROLLBACK')
        raise
    finally:
        c.close()


if __name__ == '__main__':
    app = create_app()
    init_db(app)
    with sqlite3.connect(app.config['DATABASE']) as c:
        if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            print('\nCódigo de instalación (solo primer administrador): ' + app.config['SETUP_CODE'], flush=True)
    print('Acceso: http://127.0.0.1:5000 — Para cámara móvil usa HTTPS.', flush=True)
    from waitress import serve
    serve(app, host=os.environ.get('ACCESO_HOST', '0.0.0.0'), port=int(os.environ.get('PORT', '5000')), threads=8)
