"""Copia consistente de SQLite, incluso si el servidor sigue atendiendo."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import os
import secrets

from database import TABLES, connect
from data_checks import columns, quoted, verify_copy


def backup_database(source=None, destination=None):
    root = Path(__file__).resolve().parent
    source = Path(source or root / 'tickets.db').resolve()
    if not source.is_file():
        raise FileNotFoundError('Todavía no existe la base de datos: ' + str(source))
    destination = Path(destination or root / 'backups' / ('tickets-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db'))
    if destination.exists():
        raise FileExistsError('El respaldo ya existe')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(destination)) as target:
            original.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('La comprobación del respaldo falló')
    return destination


def backup_postgres(connection, destination):
    """A restorable SQLite snapshot, including the final pre-reset transaction.

    All application writers use the same advisory lock. Reuse the caller's
    locked transaction so backup and deletion cannot be separated by a sale.
    """
    from server import create_app, init_db
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError('El respaldo ya existe')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + '.' + secrets.token_hex(8) + '.tmp')
    owns_transaction = not connection.in_transaction
    try:
        if owns_transaction:
            connection.execute('BEGIN IMMEDIATE')
        init_db(create_app(temporary))
        with closing(connect(temporary)) as target:
            target.execute('BEGIN IMMEDIATE')
            for table in TABLES:
                names = columns(target, table)
                fields = ','.join(map(quoted, names))
                insert = 'INSERT INTO ' + quoted(table) + '(' + fields + ') VALUES(' + ','.join('?' for _ in names) + ')'
                for row in connection.execute('SELECT ' + fields + ' FROM ' + quoted(table)):
                    target.execute(insert, tuple(row))
            verify_copy(connection, target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('El respaldo no supera la comprobación de integridad.')
            target.execute('COMMIT')
            target.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            target.execute('PRAGMA journal_mode=DELETE')
        with temporary.open('r+b') as saved:
            os.fsync(saved.fileno())
        # Random, private filenames prevent concurrent callers sharing a target.
        temporary.replace(destination)
        if owns_transaction:
            connection.execute('COMMIT')
        return destination
    except Exception:
        if owns_transaction and connection.in_transaction:
            connection.execute('ROLLBACK')
        raise
    finally:
        for path in (temporary, Path(str(temporary) + '-wal'), Path(str(temporary) + '-shm')):
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    print('Respaldo verificado: ' + str(backup_database()))
