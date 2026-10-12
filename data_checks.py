"""Content verification without printing personal data, passwords or QR tokens."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
import re

from database import TABLES


def quoted(name):
    if not re.fullmatch(r'[a-z_][a-z_0-9]*', name):
        raise ValueError('Identificador de base de datos no permitido.')
    return '"' + name + '"'


def columns(connection, table):
    return tuple(col[0] for col in connection.execute('SELECT * FROM ' + quoted(table) + ' LIMIT 0').description)


def fingerprint(connection, table, names):
    query = 'SELECT ' + ','.join(map(quoted, names)) + ' FROM ' + quoted(table)
    hashes = Counter()
    for row in connection.execute(query):
        # SQLite REAL affinity and PostgreSQL double precision may represent
        # an integral value differently; compare the numeric value exactly.
        values = [('number', str(Decimal(str(v)).normalize())) if isinstance(v, (int, float, Decimal))
                  else ('value', v) for v in row]
        content = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        hashes[hashlib.sha256(content).digest()] += 1
    return hashes


def verify_copy(source, target):
    counts = {}
    for table in TABLES:
        names = columns(source, table)
        if set(names) != set(columns(target, table)):
            raise RuntimeError('Columnas incompatibles: ' + table)
        original = fingerprint(source, table, names)
        copied = fingerprint(target, table, names)
        if original != copied:
            raise RuntimeError('La copia no coincide en la tabla: ' + table)
        counts[table] = sum(original.values())
    return counts
