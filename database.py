"""SQLite local y PostgreSQL, con la misma interfaz usada por el servidor."""
from decimal import Decimal
import re
import sqlite3


# A single transaction lock preserves SQLite's one-writer semantics across
# processes, including capacity checks, ticket numbering and idempotency keys.
WRITE_LOCK = 731947210
TABLES = ('users', 'events', 'tariffs', 'ticket_batches', 'tickets',
          'validations', 'payments', 'cash_movements', 'sessions',
          'operations', 'audit', 'login_attempts')


def is_postgres(database):
    return str(database).startswith(('postgresql://', 'postgres://'))


class Row:
    """Like sqlite3.Row: indexed/key access, dict(row), and value iteration."""
    def __init__(self, names, values):
        self.names = names
        self.values = tuple(int(v) if isinstance(v, Decimal) and v == v.to_integral_value()
                            else v for v in values)

    def keys(self):
        return self.names

    def __getitem__(self, key):
        return self.values[self.names.index(key)] if isinstance(key, str) else self.values[key]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)


def row_factory(cursor):
    names = tuple(col.name for col in cursor.description) if cursor.description else ()
    return lambda values: Row(names, values)


def postgres_sql(statement, parameters):
    # Protect quoted SQL identifiers/literals; all data still travels separately.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", statement)
    if parameters is not None:
        parts = [part.replace('%', '%%') for part in parts]
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r'\binstr\s*\(', 'strpos(', parts[i], flags=re.I)
        if parameters is not None:
            parts[i] = parts[i].replace('?', '%s')
    return ''.join(parts)


class PostgresConnection:
    def __init__(self, database):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError('Instala requirements-render.txt para usar PostgreSQL.') from error
        self.driver = psycopg
        try:
            self.raw = psycopg.connect(database, autocommit=True, row_factory=row_factory,
                                       prepare_threshold=None, connect_timeout=20,
                                       application_name='entrix')
        except psycopg.Error:
            raise sqlite3.OperationalError('No se pudo conectar con PostgreSQL.') from None

    @property
    def in_transaction(self):
        return self.raw.info.transaction_status != self.driver.pq.TransactionStatus.IDLE

    def begin_write(self):
        try:
            self.raw.execute('BEGIN ISOLATION LEVEL READ COMMITTED')
            self.raw.execute("SET LOCAL lock_timeout = '15s'")
            self.raw.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
        except self.driver.Error:
            self.raw.rollback()
            raise

    def execute(self, statement, parameters=None):
        own_transaction = False
        try:
            if statement.strip().upper() == 'BEGIN IMMEDIATE':
                self.begin_write()
                return None
            # Authentication and GET audit records write outside operation().
            if re.match(r'\s*(INSERT|UPDATE|DELETE)\b', statement, re.I) and not self.in_transaction:
                self.begin_write()
                own_transaction = True
            cursor = self.raw.execute(postgres_sql(statement, parameters), parameters)
            if own_transaction:
                self.raw.commit()
            return cursor
        except self.driver.Error as error:
            if own_transaction:
                self.raw.rollback()
            kind = sqlite3.IntegrityError if isinstance(error, self.driver.IntegrityError) else sqlite3.OperationalError
            # Database errors can include customer records in DETAIL: don't log them.
            raise kind('PostgreSQL: ' + (error.sqlstate or 'connection_error')) from None

    def close(self):
        self.raw.close()


def connect(database):
    if is_postgres(database):
        return PostgresConnection(database)
    connection = sqlite3.connect(database, timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def init_postgres(connection):
    from pathlib import Path
    schema = Path(__file__).with_name('postgres_schema.sql').read_text(encoding='utf-8')
    for statement in schema.split(';'):
        if statement.strip():
            connection.execute(statement)
