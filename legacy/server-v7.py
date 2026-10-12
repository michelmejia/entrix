from flask import Flask, jsonify, request, send_from_directory
import sqlite3, secrets, os
from datetime import datetime, timezone

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.path.join(BASE,'tickets.db')
app=Flask(__name__, static_folder=BASE, static_url_path='')

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def conn():
    c=sqlite3.connect(DB, timeout=10, isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    return c

def init_db():
    c=conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS events(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, date TEXT, time TEXT, location TEXT,
      capacity INTEGER NOT NULL DEFAULT 0, price REAL NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'activo', created_at TEXT NOT NULL, archived_at TEXT
    );
    CREATE TABLE IF NOT EXISTS tickets(
      id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), token TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL DEFAULT 'DISPONIBLE', price REAL NOT NULL DEFAULT 0,
      customer_name TEXT, customer_phone TEXT, paid_amount REAL NOT NULL DEFAULT 0,
      used_at TEXT, used_by TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS validations(
      id TEXT PRIMARY KEY, ticket_id TEXT, event_id TEXT NOT NULL, user_name TEXT NOT NULL,
      result TEXT NOT NULL, date TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS payments(
      id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id), amount REAL NOT NULL,
      user_name TEXT, date TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cash_movements(
      id TEXT PRIMARY KEY, type TEXT NOT NULL CHECK(type IN ('INGRESO','RETIRO')),
      amount REAL NOT NULL, description TEXT, user_name TEXT, date TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tickets_event ON tickets(event_id);
    CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
    CREATE INDEX IF NOT EXISTS idx_validations_date ON validations(date);
    CREATE INDEX IF NOT EXISTS idx_payments_ticket ON payments(ticket_id);
    ''')
    # Backward-compatible upgrades for a database created by an earlier V7.
    cols={r['name'] for r in c.execute('PRAGMA table_info(events)').fetchall()}
    if 'archived_at' not in cols: c.execute('ALTER TABLE events ADD COLUMN archived_at TEXT')
    c.close()

def token(): return secrets.token_hex(24)
def event_row(r): return {'id':r['id'],'name':r['name'],'date':r['date'],'time':r['time'],'location':r['location'],'capacity':r['capacity'],'status':r['status'],'createdAt':r['created_at']}
def ticket_row(r):
    bal=max(0,round(r['price']-r['paid_amount'],2))
    return {'id':r['id'],'eventId':r['event_id'],'token':r['token'],'status':r['status'],'price':r['price'],'customerName':r['customer_name'],'customerPhone':r['customer_phone'],'paidAmount':r['paid_amount'],'balance':bal,'usedAt':r['used_at'],'usedBy':r['used_by'],'createdAt':r['created_at']}
def validation_row(r): return {'id':r['id'],'ticketId':r['ticket_id'],'eventId':r['event_id'],'userName':r['user_name'],'result':r['result'],'date':r['date']}
def cash_row(r): return {'id':r['id'],'type':r['type'],'amount':r['amount'],'description':r['description'],'userName':r['user_name'],'date':r['date']}

@app.get('/')
def index(): return send_from_directory(BASE,'ticket-access-demo.html')
@app.get('/<path:p>')
def serve_file(p):
    if p.startswith('api/'): return ('Not found',404)
    return send_from_directory(BASE,p)

@app.get('/api/bootstrap')
def bootstrap():
    c=conn(); ev=c.execute("SELECT * FROM events WHERE status!='archivado' ORDER BY created_at DESC").fetchall(); tk=c.execute('SELECT * FROM tickets ORDER BY created_at DESC').fetchall(); va=c.execute('SELECT * FROM validations ORDER BY date DESC LIMIT 1000').fetchall(); cash=c.execute('SELECT * FROM cash_movements ORDER BY date DESC LIMIT 1000').fetchall(); c.close()
    return jsonify(events=[event_row(x) for x in ev],tickets=[ticket_row(x) for x in tk],validations=[validation_row(x) for x in va],cash=[cash_row(x) for x in cash])

@app.get('/api/validations')
def validations():
    c=conn(); rows=c.execute('SELECT * FROM validations ORDER BY date DESC LIMIT 2000').fetchall(); c.close(); return jsonify(validations=[validation_row(x) for x in rows])

@app.post('/api/events')
def create_event():
    d=request.get_json(force=True) or {}; name=(d.get('name') or '').strip(); qty=max(1,int(d.get('capacity') or d.get('qty') or 0))
    if not name or qty<1: return jsonify(error='Nombre y cantidad de entradas son obligatorios'),400
    eid='ev_'+secrets.token_hex(8); ts=now(); c=conn()
    try:
      c.execute('BEGIN IMMEDIATE')
      c.execute('INSERT INTO events(id,name,date,time,location,capacity,price,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(eid,name,d.get('date',''),d.get('time',''),d.get('location') or 'Por confirmar',qty,0,'activo',ts))
      tickets=[]
      for _ in range(qty):
        tid='tk_'+secrets.token_hex(8); tok=token(); c.execute('INSERT INTO tickets(id,event_id,token,status,price,paid_amount,created_at) VALUES(?,?,?,?,?,?,?)',(tid,eid,tok,'DISPONIBLE',0,0,ts)); tickets.append(ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()))
      c.execute('COMMIT'); ev=event_row(c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()); return jsonify(event=ev,tickets=tickets)
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.patch('/api/events/<eid>')
def edit_event(eid):
    d=request.get_json(force=True) or {}; c=conn(); r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
    if not r: c.close(); return jsonify(error='Evento no encontrado'),404
    fields=['name','date','time','location']; vals=[d.get(k,r[k]) for k in fields]
    try:
      c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE events SET name=?,date=?,time=?,location=? WHERE id=?',(*vals,eid)); c.execute('COMMIT')
      return jsonify(event=event_row(c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()))
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.patch('/api/events/<eid>/status')
def toggle(eid):
    c=conn(); r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
    if not r: c.close(); return jsonify(error='Evento no encontrado'),404
    status='inactivo' if r['status']=='activo' else 'activo'; c.execute('UPDATE events SET status=? WHERE id=?',(status,eid)); r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone(); c.close(); return jsonify(event=event_row(r))

@app.delete('/api/events/<eid>')
def archive(eid):
    c=conn(); r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
    if not r: c.close(); return jsonify(error='Evento no encontrado'),404
    c.execute("UPDATE events SET status='archivado', archived_at=? WHERE id=?",(now(),eid)); r=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone(); c.close(); return jsonify(event=event_row(r))

@app.post('/api/events/<eid>/tickets/generate')
def gen(eid):
    d=request.get_json(force=True) or {}; qty=max(1,int(d.get('qty') or 0)); c=conn(); ev=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
    if not ev: c.close(); return jsonify(error='Evento no encontrado'),404
    count=c.execute('SELECT COUNT(*) n FROM tickets WHERE event_id=?',(eid,)).fetchone()['n']
    if count+qty>ev['capacity']: c.close(); return jsonify(error=f'La capacidad del evento es {ev["capacity"]}. Ya existen {count} entradas.'),400
    ts=now(); out=[]; c.execute('BEGIN IMMEDIATE')
    try:
      for _ in range(qty):
        tid='tk_'+secrets.token_hex(8); tok=token(); c.execute('INSERT INTO tickets(id,event_id,token,status,price,paid_amount,created_at) VALUES(?,?,?,?,?,?,?)',(tid,eid,tok,'DISPONIBLE',0,0,ts)); out.append(ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()))
      c.execute('COMMIT'); return jsonify(tickets=out)
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.post('/api/tickets/<tid>/sale')
def sale(tid):
    d=request.get_json(force=True) or {}; name=(d.get('customerName') or '').strip(); phone=(d.get('customerPhone') or '').strip(); price=max(0,float(d.get('price') or 0)); amount=max(0,float(d.get('initialPayment') or 0)); c=conn()
    try:
      c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
      if not t: raise ValueError('Entrada no encontrada')
      if t['status']!='DISPONIBLE': raise ValueError('Esta entrada ya fue vendida, usada o cancelada')
      if not name: raise ValueError('Ingresa el nombre del comprador')
      if price<=0: raise ValueError('La tarifa de esta entrada es obligatoria y debe ser mayor a 0')
      if amount>price: raise ValueError('El pago no puede superar la tarifa')
      paid=round(amount,2); c.execute("UPDATE tickets SET status='VENDIDA',customer_name=?,customer_phone=?,price=?,paid_amount=? WHERE id=? AND status='DISPONIBLE'",(name,phone,price,paid,tid))
      if amount>0: c.execute('INSERT INTO payments(id,ticket_id,amount,user_name,date) VALUES(?,?,?,?,?)',('pay_'+secrets.token_hex(8),tid,amount,'Administrador',now())); c.execute('INSERT INTO cash_movements(id,type,amount,description,user_name,date) VALUES(?,?,?,?,?,?)',('cash_'+secrets.token_hex(8),'INGRESO',amount,f'Pago entrada #{tid}','Administrador',now()))
      c.execute('COMMIT'); return jsonify(ticket=ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()))
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.post('/api/tickets/<tid>/payment')
def payment(tid):
    d=request.get_json(force=True) or {}; amount=max(0,float(d.get('amount') or 0)); user=(d.get('userName') or 'Validador / puerta').strip(); c=conn()
    try:
      c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
      if not t: raise ValueError('Entrada no encontrada')
      if t['status']!='VENDIDA': raise ValueError('La entrada no está en estado VENDIDA')
      balance=round(t['price']-t['paid_amount'],2)
      if amount<=0: raise ValueError('Ingresa un monto mayor a 0')
      if amount>balance+0.001: raise ValueError(f'El saldo pendiente es S/ {balance:.2f}')
      paid=round(t['paid_amount']+amount,2); c.execute('UPDATE tickets SET paid_amount=? WHERE id=?',(paid,tid)); c.execute('INSERT INTO payments(id,ticket_id,amount,user_name,date) VALUES(?,?,?,?,?)',('pay_'+secrets.token_hex(8),tid,amount,user,now())); c.execute('INSERT INTO cash_movements(id,type,amount,description,user_name,date) VALUES(?,?,?,?,?,?)',('cash_'+secrets.token_hex(8),'INGRESO',amount,f'Abono entrada #{tid}',user,now()))
      c.execute('COMMIT'); return jsonify(ticket=ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()))
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.post('/api/tickets/<tid>/cancel')
def cancel_ticket(tid):
    c=conn();
    try:
      c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
      if not t: raise ValueError('Entrada no encontrada')
      if t['status']=='USADA': raise ValueError('No se puede cancelar una entrada usada')
      c.execute("UPDATE tickets SET status='CANCELADA' WHERE id=?",(tid,)); c.execute('COMMIT'); return jsonify(ticket=ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()))
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.post('/api/validate')
def validate():
    d=request.get_json(force=True) or {}; tok=(d.get('token') or '').strip(); eid=str(d.get('eventId') or ''); user=(d.get('userName') or 'Validador').strip(); c=conn(); vid='val_'+secrets.token_hex(8); ts=now()
    try:
      c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM tickets WHERE token=?',(tok,)).fetchone(); result='INVALID'; reason=None; ticket=None
      if not t: reason='token no existe'
      elif t['event_id']!=eid: reason='otro evento'
      elif t['status']=='USADA': result='USED'
      elif t['status']=='CANCELADA': result='CANCELLED'
      elif t['status']=='DISPONIBLE': result='NOT_SOLD'
      elif round(t['price']-t['paid_amount'],2)>0: result='PAYMENT_PENDING'
      else:
        cur=c.execute("UPDATE tickets SET status='USADA',used_at=?,used_by=? WHERE id=? AND status='VENDIDA' AND round(price-paid_amount,2)<=0",(ts,user,t['id']))
        if cur.rowcount==1: result='VALID'
        else:
          t=c.execute('SELECT * FROM tickets WHERE id=?',(t['id'],)).fetchone(); result='USED' if t['status']=='USADA' else ('PAYMENT_PENDING' if round(t['price']-t['paid_amount'],2)>0 else 'INVALID')
      if t: ticket=ticket_row(c.execute('SELECT * FROM tickets WHERE id=?',(t['id'],)).fetchone())
      c.execute('INSERT INTO validations(id,ticket_id,event_id,user_name,result,date) VALUES(?,?,?,?,?,?)',(vid,t['id'] if t else None,eid,user,result,ts)); c.execute('COMMIT')
      v={'id':vid,'ticketId':t['id'] if t else None,'eventId':eid,'userName':user,'result':result,'date':ts}
      rr={'result':result,'reason':reason,'usedAt':ticket['usedAt'] if ticket else None,'usedBy':ticket['usedBy'] if ticket else None,'balance':ticket['balance'] if ticket else None,'ticket':ticket}
      return jsonify(result=rr,validation=v,ticket=ticket)
    except Exception as e: c.execute('ROLLBACK'); return jsonify(error=str(e)),400
    finally: c.close()

@app.get('/api/cash')
def cash():
    c=conn(); rows=c.execute('SELECT * FROM cash_movements ORDER BY date DESC LIMIT 2000').fetchall(); c.close(); return jsonify(cash=[cash_row(x) for x in rows])

@app.post('/api/cash')
def cash_add():
    d=request.get_json(force=True) or {}; typ=d.get('type'); amount=max(0,float(d.get('amount') or 0)); desc=(d.get('description') or '').strip(); user=(d.get('userName') or 'Administrador').strip()
    if typ not in ('INGRESO','RETIRO') or amount<=0: return jsonify(error='Tipo y monto válidos son obligatorios'),400
    c=conn(); mid='cash_'+secrets.token_hex(8); c.execute('INSERT INTO cash_movements(id,type,amount,description,user_name,date) VALUES(?,?,?,?,?,?)',(mid,typ,amount,desc,user,now())); r=c.execute('SELECT * FROM cash_movements WHERE id=?',(mid,)).fetchone(); c.close(); return jsonify(movement=cash_row(r))

if __name__=='__main__':
    init_db(); print('Sistema QR V7: http://127.0.0.1:5000/')
    print('Para celular: http://IP-DE-ESTA-PC:5000/ (misma red Wi-Fi)'); app.run(host='0.0.0.0',port=5000,debug=False)
