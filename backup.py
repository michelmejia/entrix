"""Copia consistente de SQLite, incluso si el servidor sigue atendiendo."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3


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


if __name__ == '__main__':
    print('Respaldo verificado: ' + str(backup_database()))
