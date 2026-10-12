"""Copy a consistent SQLite snapshot to an EMPTY PostgreSQL database.

Default: inspect only. --apply --source-stopped is required to copy data.
Credentials come from ENTRIX_NEON_URL / DATABASE_URL or a hidden prompt.
"""
import argparse
from contextlib import closing
from datetime import datetime
import getpass
import os
from pathlib import Path
import secrets
import sqlite3

from backup import backup_database
from database import TABLES, connect, init_postgres, is_postgres
from data_checks import columns, quoted, verify_copy
from server import create_app, init_db


def snapshot_source(source):
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError('No existe la base SQLite indicada.')
    target = source.parent / 'backups' / ('before-neon-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4) + '.db')
    backup_database(source, target)
    # Run legacy migrations only on our copy, never on the operating database.
    init_db(create_app(target))
    with closing(connect(target)) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != set(TABLES):
            raise RuntimeError('El origen contiene tablas faltantes o desconocidas. Revisar antes de migrar.')
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('La base de origen no supera la comprobación de integridad.')
    return target


def target_tables(connection):
    return {r[0] for r in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'")}


def require_empty(connection):
    present = target_tables(connection)
    if present - set(TABLES):
        raise RuntimeError('El destino contiene tablas ajenas a Entrix; usa una base vacía dedicada.')
    for table in present:
        if connection.execute('SELECT 1 FROM ' + quoted(table) + ' LIMIT 1').fetchone():
            raise RuntimeError('El destino ya contiene datos en ' + table + '. No se sobrescribió nada.')


def migrate(snapshot, destination, apply=False):
    if not is_postgres(destination):
        raise ValueError('Se requiere una URL PostgreSQL de Neon.')
    with closing(connect(snapshot)) as source, closing(connect(destination)) as target:
        target.execute('BEGIN IMMEDIATE' if apply else 'BEGIN READ ONLY')
        try:
            require_empty(target)
            if not apply:
                counts = {table: source.execute('SELECT count(*) FROM ' + quoted(table)).fetchone()[0] for table in TABLES}
                target.execute('ROLLBACK')
                return counts
            init_postgres(target)
            # DDL, copy and verification commit together; any failure rolls back.
            for table in TABLES:
                names = columns(source, table)
                if set(names) != set(columns(target, table)):
                    raise RuntimeError('Columnas incompatibles en ' + table)
                fields = ','.join(map(quoted, names))
                insert = 'INSERT INTO ' + quoted(table) + '(' + fields + ') VALUES(' + ','.join('?' for _ in names) + ')'
                for row in source.execute('SELECT ' + fields + ' FROM ' + quoted(table)):
                    target.execute(insert, tuple(row))
            counts = verify_copy(source, target)
            target.execute('COMMIT')
            return counts
        except Exception:
            if target.in_transaction:
                target.execute('ROLLBACK')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('tickets.db'))
    parser.add_argument('--apply', action='store_true', help='Copiar y confirmar la migración verificada.')
    parser.add_argument('--source-stopped', action='store_true', help='Confirma que detuviste todas las ventas y validaciones locales.')
    args = parser.parse_args()
    if args.apply and not args.source_stopped:
        parser.error('Detén el servidor local y usa --apply --source-stopped para la copia definitiva.')
    destination = os.environ.get('ENTRIX_NEON_URL') or os.environ.get('DATABASE_URL')
    if not destination:
        destination = getpass.getpass('URL directa de Neon (oculta): ').strip()
    try:
        snapshot = snapshot_source(args.source)
        print('Respaldo local:', snapshot)
        counts = migrate(snapshot, destination, apply=args.apply)
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
        print('No se completó la migración:', error)
        return 1
    for table, count in counts.items():
        print(f'{table}: {count}')
    print('Migración confirmada: todos los registros coinciden.' if args.apply else
          'Comprobación correcta. No se modificó Neon. La copia definitiva requiere detener el servidor local.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
