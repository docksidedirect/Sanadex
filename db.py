# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - data layer.

Runs on SQLite out of the box (zero install) and can be pointed at Microsoft
SQL Server through pyodbc when ``SANADEX_DB=mssql`` is set.

The schema is declared once, as Python data, and rendered for the active
dialect, so both engines stay in sync and existing databases are upgraded in
place (missing columns are added, never dropped).
"""
import os
import shutil
import sqlite3
import time

APP_NAME = 'Sanadex.wanis.online'
BASE = os.path.dirname(os.path.abspath(__file__))
DIALECT = (os.environ.get('SANADEX_DB') or 'sqlite').strip().lower()
MSSQL_DSN = os.environ.get('SANADEX_MSSQL_DSN', '')

if DIALECT in ('mssql', 'sqlserver', 'sql-server'):
    DIALECT = 'mssql'
else:
    DIALECT = 'sqlite'

DB_PATH = os.environ.get('SANADEX_DB_PATH') or os.path.join(BASE, 'sanadex.db')
BACKUP_DIR = os.environ.get('SANADEX_BACKUP_DIR') or os.path.join(BASE, 'backups')
UPLOAD_DIR = os.environ.get('SANADEX_UPLOAD_DIR') or os.path.join(BASE, 'uploads')
LEGACY_DB = os.path.join(BASE, 'sahm.db')
KEEP_BACKUPS = 40
SCHEMA_VERSION = 4

# --------------------------------------------------------------------------- #
# schema declaration:  table -> [(column, kind, default)]
# kind: int | real | str(200) | text | ts
# --------------------------------------------------------------------------- #
TABLES = {
    'company': [
        ('name', 'str', ''), ('phone', 'str', ''), ('address', 'str', ''), ('email', 'str', ''),
        ('tax_number', 'str', ''), ('cr_number', 'str', ''), ('logo', 'str', ''), ('currency', 'str', ''),
        ('company_type', 'str', 'retail'), ('lang', 'str', 'ar'), ('accent', 'str', 'clay'),
        ('inv_prefix', 'str', 'INV-'), ('inv_suffix', 'str', ''),
        ('pinv_prefix', 'str', 'PUR-'), ('pinv_suffix', 'str', ''),
        ('order_prefix', 'str', 'SO-'), ('vch_prefix', 'str', 'VCH-'),
        ('next_sale_no', 'int', 1), ('next_purchase_no', 'int', 1),
        ('next_order_no', 'int', 1), ('next_voucher_no', 'int', 1),
        ('tax_percent', 'real', 14), ('auto_backup', 'int', 0), ('backup_hours', 'int', 24),
        ('allow_signup', 'int', 1),
        ('last_backup', 'ts', 0), ('expiry_alert_days', 'int', 30), ('restrict_scope', 'int', 1),
        ('gdrive_enabled', 'int', 0), ('gdrive_client_id', 'str', ''), ('gdrive_client_secret', 'str', ''),
        ('gdrive_refresh_token', 'text', ''), ('gdrive_folder_id', 'str', ''),
        ('gdrive_last_sync', 'ts', 0), ('gdrive_email', 'str', ''),
        ('invoice_terms', 'text', ''), ('receipt_footer', 'str', ''),
        ('theme', 'str', 'light'), ('show_cost', 'int', 1),
        ('telegram_enabled', 'int', 0), ('telegram_token', 'text', ''),
        ('telegram_chat_id', 'str', ''), ('telegram_digest_hour', 'int', 21),
        ('public_url', 'str', ''),
    ],
    'roles': [
        ('name', 'str', ''), ('notes', 'str', ''), ('is_system', 'int', 0),
        ('p_sell', 'int', 0), ('p_buy', 'int', 0), ('p_inv', 'int', 0), ('p_treasury', 'int', 0),
        ('p_reports', 'int', 0), ('p_settings', 'int', 0), ('p_hr', 'int', 0), ('p_acc', 'int', 0),
        ('p_services', 'int', 0), ('p_inst', 'int', 0),
    ],
    'users': [
        ('username', 'str', ''), ('password', 'text', ''), ('full_name', 'str', ''), ('phone', 'str', ''),
        ('email', 'str', ''), ('role_id', 'int', 0), ('treasury_id', 'int', 0), ('store_id', 'int', 0),
        ('is_admin', 'int', 0), ('active', 'int', 1), ('lang', 'str', ''),
        ('created_at', 'ts', 0), ('last_login', 'ts', 0), ('notes', 'str', ''),
        ('trial_started', 'ts', 0), ('licensed', 'int', 0),
    ],
    'stores': [('name', 'str', ''), ('location', 'str', ''), ('phone', 'str', ''),
               ('manager', 'str', ''), ('active', 'int', 1)],
    'treasuries': [('name', 'str', ''), ('ttype', 'str', 'cash'), ('balance', 'real', 0),
                   ('account_no', 'str', ''), ('iban', 'str', ''), ('bank_name', 'str', ''),
                   ('active', 'int', 1)],
    'treasury_transfers': [('tfrom', 'int', 0), ('tto', 'int', 0), ('amount', 'real', 0),
                           ('tdate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0),
                           ('ttime', 'ts', 0), ('number', 'str', '')],
    'price_tiers': [('name', 'str', ''), ('percent', 'real', 0), ('is_default', 'int', 0),
                    ('notes', 'str', '')],
    'customers': [('name', 'str', ''), ('phone', 'str', ''), ('email', 'str', ''), ('address', 'str', ''),
                  ('tax_number', 'str', ''), ('tier_id', 'int', 0), ('credit_limit', 'real', 0),
                  ('opening_balance', 'real', 0), ('balance', 'real', 0), ('active', 'int', 1),
                  ('notes', 'str', '')],
    'suppliers': [('name', 'str', ''), ('phone', 'str', ''), ('email', 'str', ''), ('address', 'str', ''),
                  ('tax_number', 'str', ''), ('opening_balance', 'real', 0), ('balance', 'real', 0),
                  ('active', 'int', 1), ('notes', 'str', '')],
    'items': [('code', 'str', ''), ('name', 'str', ''), ('name_en', 'str', ''), ('category', 'str', ''),
              ('brand', 'str', ''), ('purchase_price', 'real', 0), ('sale_price', 'real', 0),
              ('min_price', 'real', 0), ('reorder_level', 'real', 0), ('has_expiry', 'int', 0),
              ('notes', 'str', ''), ('active', 'int', 1)],
    'item_units': [('item_id', 'int', 0), ('name', 'str', ''), ('factor', 'real', 1), ('barcode', 'str', ''),
                   ('sale_price', 'real', 0), ('purchase_price', 'real', 0), ('is_base', 'int', 0)],
    'item_batches': [('item_id', 'int', 0), ('store_id', 'int', 0), ('qty', 'real', 0),
                     ('expiry_date', 'str', ''), ('batch_no', 'str', '')],
    'stock_movements': [('item_id', 'int', 0), ('unit_id', 'int', 0), ('store_id', 'int', 0),
                        ('qty', 'real', 0), ('ref_type', 'str', ''), ('ref_id', 'int', 0),
                        ('mdate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0)],
    'invoices': [('number', 'str', ''), ('itype', 'str', 'sale'), ('idate', 'str', ''),
                 ('due_date', 'str', ''), ('party_type', 'str', ''), ('party_id', 'int', 0),
                 ('store_id', 'int', 0), ('treasury_id', 'int', 0), ('subtotal', 'real', 0),
                 ('discount', 'real', 0), ('tax', 'real', 0), ('tax_percent', 'real', 0),
                 ('net', 'real', 0), ('paid', 'real', 0), ('status', 'str', 'paid'),
                 ('notes', 'str', ''), ('ref', 'str', ''), ('user_id', 'int', 0),
                 ('is_tax', 'int', 0), ('ctime', 'ts', 0), ('order_id', 'int', 0)],
    'invoice_items': [('invoice_id', 'int', 0), ('item_id', 'int', 0), ('unit_id', 'int', 0),
                      ('qty', 'real', 0), ('price', 'real', 0), ('cost', 'real', 0),
                      ('discount', 'real', 0), ('total', 'real', 0), ('expiry_date', 'str', ''),
                      ('tier_pct', 'real', 0)],
    'doc_orders': [('number', 'str', ''), ('otype', 'str', 'sale'), ('party_id', 'int', 0),
                   ('odate', 'str', ''), ('due_date', 'str', ''), ('status', 'str', 'pending'),
                   ('notes', 'str', ''), ('user_id', 'int', 0), ('ctime', 'ts', 0),
                   ('invoice_id', 'int', 0)],
    'doc_order_items': [('order_id', 'int', 0), ('item_id', 'int', 0), ('unit_id', 'int', 0),
                        ('qty', 'real', 0), ('price', 'real', 0)],
    'transfers': [('number', 'str', ''), ('from_store', 'int', 0), ('to_store', 'int', 0),
                  ('tdate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0), ('ctime', 'ts', 0)],
    'transfer_items': [('transfer_id', 'int', 0), ('item_id', 'int', 0), ('unit_id', 'int', 0),
                       ('qty', 'real', 0)],
    'damages': [('item_id', 'int', 0), ('store_id', 'int', 0), ('qty', 'real', 0), ('unit_id', 'int', 0),
                ('ddate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0), ('reason', 'str', '')],
    'withdrawals': [('treasury_id', 'int', 0), ('amount', 'real', 0), ('wtype', 'str', 'personal'),
                    ('wdate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0)],
    'vouchers': [('number', 'str', ''), ('vtype', 'str', 'receipt'), ('treasury_id', 'int', 0),
                 ('party_type', 'str', ''), ('party_id', 'int', 0), ('amount', 'real', 0),
                 ('vdate', 'str', ''), ('note', 'str', ''), ('user_id', 'int', 0),
                 ('ctime', 'ts', 0), ('cheque_id', 'int', 0)],
    'credit_notes': [('number', 'str', ''), ('party_type', 'str', 'customer'), ('party_id', 'int', 0),
                     ('ctype', 'str', 'discount'), ('amount', 'real', 0), ('cdate', 'str', ''),
                     ('note', 'str', ''), ('user_id', 'int', 0), ('ctime', 'ts', 0)],
    'reminders': [('title', 'str', ''), ('rdate', 'str', ''), ('note', 'str', ''),
                  ('done', 'int', 0), ('user_id', 'int', 0), ('priority', 'str', 'normal'),
                  ('ref_type', 'str', ''), ('ref_id', 'int', 0)],
    'offers': [('name', 'str', ''), ('item_id', 'int', 0), ('discount_percent', 'real', 0),
               ('start_date', 'str', ''), ('end_date', 'str', ''), ('active', 'int', 1),
               ('scope', 'str', 'item')],
    'accounts_chart': [('code', 'str', ''), ('name', 'str', ''), ('name_en', 'str', ''),
                       ('parent_id', 'int', 0), ('atype', 'str', 'asset'), ('is_group', 'int', 0),
                       ('active', 'int', 1)],
    'journals': [('number', 'str', ''), ('jdate', 'str', ''), ('note', 'str', ''),
                 ('user_id', 'int', 0), ('ctime', 'ts', 0), ('ref', 'str', '')],
    'journal_lines': [('journal_id', 'int', 0), ('account_id', 'int', 0), ('debit', 'real', 0),
                      ('credit', 'real', 0), ('note', 'str', '')],
    'installment_plans': [('customer_id', 'int', 0), ('invoice_id', 'int', 0), ('total', 'real', 0),
                          ('count', 'int', 0), ('period', 'str', 'month'), ('start_date', 'str', ''),
                          ('notes', 'str', ''), ('user_id', 'int', 0), ('ctime', 'ts', 0),
                          ('down_payment', 'real', 0)],
    'installments': [('plan_id', 'int', 0), ('due_date', 'str', ''), ('amount', 'real', 0),
                     ('paid', 'int', 0), ('paid_date', 'str', ''), ('paid_amount', 'real', 0),
                     ('treasury_id', 'int', 0)],
    'job_roles': [('name', 'str', ''), ('notes', 'str', '')],
    'salary_packages': [('name', 'str', ''), ('basic', 'real', 0), ('housing', 'real', 0),
                        ('transport', 'real', 0), ('allowance', 'real', 0), ('notes', 'str', '')],
    'employees': [('name', 'str', ''), ('phone', 'str', ''), ('email', 'str', ''),
                  ('national_id', 'str', ''), ('job_role_id', 'int', 0), ('package_id', 'int', 0),
                  ('salary_type', 'str', 'monthly'), ('salary', 'real', 0), ('daily_rate', 'real', 0),
                  ('hire_date', 'str', ''), ('active', 'int', 1), ('notes', 'str', '')],
    'attendance': [('employee_id', 'int', 0), ('adate', 'str', ''), ('status', 'str', 'present'),
                   ('hours', 'real', 0), ('note', 'str', '')],
    'holidays': [('name', 'str', ''), ('hdate', 'str', ''), ('htype', 'str', 'public'),
                 ('notes', 'str', '')],
    'emp_payments': [('employee_id', 'int', 0), ('ptype', 'str', 'bonus'), ('amount', 'real', 0),
                     ('pdate', 'str', ''), ('note', 'str', '')],
    'advances': [('employee_id', 'int', 0), ('amount', 'real', 0), ('adate', 'str', ''),
                 ('note', 'str', ''), ('deducted', 'int', 0)],
    'payroll_runs': [('month', 'str', ''), ('run_date', 'str', ''), ('note', 'str', ''),
                     ('user_id', 'int', 0), ('total_net', 'real', 0), ('ctime', 'ts', 0)],
    'payslips': [('run_id', 'int', 0), ('employee_id', 'int', 0), ('base', 'real', 0),
                 ('bonus', 'real', 0), ('deduction', 'real', 0), ('advance', 'real', 0),
                 ('net', 'real', 0), ('att_days', 'real', 0)],
    'service_orders': [('number', 'str', ''), ('customer_name', 'str', ''), ('phone', 'str', ''),
                       ('device', 'str', ''), ('problem', 'str', ''), ('cost', 'real', 0),
                       ('paid', 'real', 0), ('status', 'str', 'received'), ('sdate', 'str', ''),
                       ('delivery_date', 'str', ''), ('technician', 'str', ''), ('notes', 'str', ''),
                       ('user_id', 'int', 0), ('ctime', 'ts', 0)],
    'cheques': [('number', 'str', ''), ('ctype', 'str', 'in'), ('party_type', 'str', 'customer'),
                ('party_id', 'int', 0), ('bank', 'str', ''), ('amount', 'real', 0),
                ('due_date', 'str', ''), ('status', 'str', 'pending'), ('treasury_id', 'int', 0),
                ('note', 'str', ''), ('user_id', 'int', 0), ('ctime', 'ts', 0)],
    'audit_log': [('ts', 'ts', 0), ('user_id', 'int', 0), ('username', 'str', ''),
                  ('action', 'str', ''), ('entity', 'str', ''), ('entity_id', 'int', 0),
                  ('detail', 'str', ''), ('ip', 'str', '')],
    'app_meta': [('k', 'str', ''), ('v', 'text', '')],
    'licenses': [('key', 'text', ''), ('customer', 'str', ''), ('edition', 'str', 'standard'),
                 ('seats', 'int', 1), ('issued', 'str', ''), ('expires', 'str', ''),
                 ('machine', 'str', ''), ('status', 'str', 'active'), ('created_at', 'ts', 0),
                 ('created_by', 'str', ''), ('note', 'str', '')],
    # Per-account subscriptions: requested at sign-up, activated/renewed by the admin.
    'subscriptions': [('user_id', 'int', 0), ('plan', 'str', ''), ('price', 'real', 0),
                      ('days', 'int', 0), ('starts', 'ts', 0), ('expires', 'ts', 0),
                      ('status', 'str', 'pending'), ('created_at', 'ts', 0),
                      ('created_by', 'str', ''), ('note', 'str', '')],
}

INDEXES = [
    ('ix_users_name', 'users', ['username'], True),
    ('ix_meta_key', 'app_meta', ['k'], True),
    ('ix_mov_item', 'stock_movements', ['item_id', 'store_id'], False),
    ('ix_mov_ref', 'stock_movements', ['ref_type', 'ref_id'], False),
    ('ix_ii_inv', 'invoice_items', ['invoice_id'], False),
    ('ix_inv_date', 'invoices', ['itype', 'idate'], False),
    ('ix_inv_party', 'invoices', ['party_type', 'party_id'], False),
    ('ix_item_units_item', 'item_units', ['item_id'], False),
    ('ix_batch_item', 'item_batches', ['item_id', 'store_id'], False),
    ('ix_ord_type', 'doc_orders', ['otype', 'status'], False),
    ('ix_audit_ts', 'audit_log', ['ts'], False),
    ('ix_att_emp', 'attendance', ['employee_id', 'adate'], True),
    ('ix_jlines', 'journal_lines', ['journal_id'], False),
    ('ix_inst_plan', 'installments', ['plan_id'], False),
    ('ix_sub_user', 'subscriptions', ['user_id', 'status'], False),
]

KIND_SQLITE = {'int': 'INTEGER', 'real': 'REAL', 'str': 'TEXT', 'text': 'TEXT', 'ts': 'REAL'}
KIND_MSSQL = {'int': 'BIGINT', 'real': 'FLOAT', 'str': 'NVARCHAR(200)', 'text': 'NVARCHAR(MAX)',
              'ts': 'FLOAT'}


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #
class Cursor:
    """Cursor proxy: identical dict rows on SQLite and SQL Server."""

    def __init__(self, cur, dialect='sqlite', lastrowid=None):
        self._cur = cur
        self.dialect = dialect
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        rid = self._lastrowid
        if rid is None:
            rid = getattr(self._cur, 'lastrowid', None)
        return rid

    def _row(self, raw):
        if raw is None:
            return None
        if self.dialect == 'mssql':
            cols = [d[0] for d in (self._cur.description or [])]
            return dict(zip(cols, tuple(raw)))
        return raw

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        rows = self._cur.fetchall()
        if self.dialect == 'mssql':
            return [self._row(r) for r in rows]
        return rows

    @property
    def rowcount(self):
        return self._cur.rowcount


class Conn:
    def __init__(self, raw, dialect):
        self.raw = raw
        self.dialect = dialect

    def execute(self, sql, args=()):
        args = tuple(args or ())
        if self.dialect == 'mssql':
            cur = self.raw.cursor()
            low = sql.lstrip().lower()
            if low.startswith('insert into'):
                cur.execute('SET NOCOUNT ON; ' + sql + '; SELECT SCOPE_IDENTITY()')
                row = cur.fetchone()
                rid = int(row[0]) if row and row[0] is not None else None
                return Cursor(cur, self.dialect, rid)
            cur.execute(sql, args)
            return Cursor(cur, self.dialect)
        cur = self.raw.execute(sql, args)
        return Cursor(cur, self.dialect)

    def executemany(self, sql, seq):
        cur = self.raw.executemany(sql, seq)
        return Cursor(cur, self.dialect)

    def executescript(self, sql):
        if self.dialect == 'mssql':
            for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
                self.raw.cursor().execute(stmt)
        else:
            self.raw.executescript(sql)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


def connect():
    if DIALECT == 'mssql':
        import pyodbc  # noqa: F401  (optional dependency)
        raw = pyodbc.connect(MSSQL_DSN, autocommit=False)
        return Conn(raw, 'mssql')
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return Conn(conn, 'sqlite')


# --------------------------------------------------------------------------- #
# query helpers
# --------------------------------------------------------------------------- #
def q(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, tuple(args or ())).fetchall()]


def one(conn, sql, args=()):
    r = conn.execute(sql, tuple(args or ())).fetchone()
    if not r:
        return None
    return dict(r)


def scalar(conn, sql, args=(), default=0):
    r = conn.execute(sql, tuple(args or ())).fetchone()
    if not r:
        return default
    val = list(dict(r).values())[0] if not isinstance(r, (list, tuple)) else r[0]
    return default if val is None else val


def x(conn, sql, args=()):
    cur = conn.execute(sql, tuple(args or ()))
    conn.commit()
    return cur.lastrowid


def ex(conn, sql, args=()):
    conn.execute(sql, tuple(args or ()))
    conn.commit()


def today():
    return time.strftime('%Y-%m-%d')


def now_ts():
    return time.time()


def add_period(date_str, period, k):
    """Shift an ISO date by k periods (day / week / month / year)."""
    try:
        y, m, d = [int(v) for v in (date_str or today()).split('-')[:3]]
    except Exception:
        y, m, d = [int(v) for v in today().split('-')]
    if period == 'day':
        t = time.mktime((y, m, d + k, 12, 0, 0, 0, 0, -1))
        return time.strftime('%Y-%m-%d', time.localtime(t))
    if period == 'week':
        return add_period(f'{y:04d}-{m:02d}-{d:02d}', 'day', 7 * k)
    if period == 'year':
        k *= 12
    total = (y * 12 + (m - 1)) + k
    y2, m2 = divmod(total, 12)
    m2 += 1
    # clamp day to month length
    lengths = [31, 29 if (y2 % 4 == 0 and (y2 % 100 != 0 or y2 % 400 == 0)) else 28,
               31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    d2 = min(d, lengths[m2 - 1])
    return f'{y2:04d}-{m2:02d}-{d2:02d}'


# --------------------------------------------------------------------------- #
# DDL generation / migration
# --------------------------------------------------------------------------- #
def _default_sql(kind, default):
    if kind in ('int',):
        return str(int(default or 0))
    if kind in ('real', 'ts'):
        return repr(float(default or 0))
    return "'" + str(default or '').replace("'", "''") + "'"


def _column_ddl(dialect, name, kind, default):
    typ = (KIND_MSSQL if dialect == 'mssql' else KIND_SQLITE)[kind]
    if dialect == 'mssql':
        return f'{name} {typ} NULL DEFAULT {_default_sql(kind, default)}'
    return f'{name} {typ} DEFAULT {_default_sql(kind, default)}'


def create_ddl(dialect):
    out = []
    for table, cols in TABLES.items():
        pk = 'BIGINT IDENTITY(1,1) PRIMARY KEY' if dialect == 'mssql' else 'INTEGER PRIMARY KEY'
        body = [f'id {pk}'] + [_column_ddl(dialect, c[0], c[1], c[2]) for c in cols]
        if dialect == 'mssql':
            out.append(f"IF OBJECT_ID('{table}', 'U') IS NULL CREATE TABLE {table}({', '.join(body)})")
        else:
            out.append(f"CREATE TABLE IF NOT EXISTS {table}({', '.join(body)})")
    return ';\n'.join(out) + ';'


def index_ddl(dialect, name, table, cols, uniq):
    col_sql = ', '.join(cols)
    if dialect == 'mssql':
        return (f"IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = '{name}') "
                f"CREATE {'UNIQUE ' if uniq else ''}INDEX {name} ON {table}({col_sql})")
    return f"CREATE {'UNIQUE ' if uniq else ''}INDEX IF NOT EXISTS {name} ON {table}({col_sql})"


def existing_columns(conn, table):
    if conn.dialect == 'mssql':
        rows = q(conn, 'SELECT COLUMN_NAME c FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME=?',
                 (table,))
        return {r['c'] for r in rows}
    return {r['name'] for r in q(conn, f'PRAGMA table_info({table})')}


def migrate(conn):
    """Add any column declared in TABLES but missing from an existing database."""
    added = []
    for table, cols in TABLES.items():
        have = existing_columns(conn, table)
        if not have:
            continue
        for name, kind, default in cols:
            if name in have:
                continue
            typ = (KIND_MSSQL if conn.dialect == 'mssql' else KIND_SQLITE)[kind]
            try:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {typ} '
                             f'DEFAULT {_default_sql(kind, default)}')
                added.append(f'{table}.{name}')
            except Exception:
                pass  # column already there under a race, or backend limitation
    if added:
        conn.commit()
    return added


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #
CHART_SEED = [
    ('1000', 'الأصول', 'Assets', 0, 'asset', 1),
    ('1100', 'النقدية والبنوك', 'Cash & banks', 1, 'asset', 1),
    ('1110', 'الصندوق', 'Cash on hand', 2, 'asset', 0),
    ('1120', 'البنك', 'Bank', 2, 'asset', 0),
    ('1200', 'العملاء (مدينون)', 'Accounts receivable', 1, 'asset', 0),
    ('1300', 'المخزون', 'Inventory', 1, 'asset', 0),
    ('2000', 'الخصوم', 'Liabilities', 0, 'liability', 1),
    ('2100', 'الموردون (دائنون)', 'Accounts payable', 7, 'liability', 0),
    ('2200', 'ضريبة القيمة المضافة', 'VAT payable', 7, 'liability', 0),
    ('3000', 'حقوق الملكية', 'Equity', 0, 'equity', 1),
    ('3100', 'رأس المال', 'Capital', 10, 'equity', 0),
    ('3200', 'أرباح محتجزة', 'Retained earnings', 10, 'equity', 0),
    ('4000', 'الإيرادات', 'Revenue', 0, 'revenue', 1),
    ('4100', 'المبيعات', 'Sales', 13, 'revenue', 0),
    ('5000', 'المصروفات', 'Expenses', 0, 'expense', 1),
    ('5100', 'تكلفة البضاعة المباعة', 'Cost of goods sold', 15, 'expense', 0),
    ('5200', 'مصروفات عمومية', 'Operating expenses', 15, 'expense', 0),
    ('5300', 'التوالف', 'Damaged stock', 15, 'expense', 0),
]


INITIAL_ADMIN_FILE = os.path.join(BASE, 'ADMIN_PASSWORD.txt')
_announced = {'done': False}


def initial_admin_password():
    """Password for the very first administrator of a brand new database.

    Never a well-known default: use SANADEX_ADMIN_PASSWORD when you want to pin
    it (deployments, automation), otherwise a random password is generated and
    written to ADMIN_PASSWORD.txt next to the app.
    """
    env = os.environ.get('SANADEX_ADMIN_PASSWORD')
    if env and len(env) >= 6:
        return env, False
    import secrets
    return secrets.token_urlsafe(9), True


def _announce_initial_password(password, generated):
    if _announced['done']:
        return
    _announced['done'] = True
    try:
        with open(INITIAL_ADMIN_FILE, 'w', encoding='utf-8') as handle:
            handle.write('Sanadex.wanis.online — first administrator password\n')
            handle.write(f'username: admin\npassword: {password}\n')
            handle.write('Sign in, then change it from Users and delete this file.\n')
    except OSError:
        pass
    print('')
    print('  ============================================================')
    print('   First administrator created for this database')
    print(f'   username: admin')
    print(f'   password: {password}')
    if generated:
        print(f'   (also written to {INITIAL_ADMIN_FILE})')
    print('   Change it right after signing in; delete the file afterwards.')
    print('  ============================================================')
    print('')


def _existing_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    found = []
    for name in os.listdir(BACKUP_DIR):
        if not name.lower().endswith('.db'):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            if os.path.getsize(path) > 0:
                found.append((os.path.getmtime(path), path))
        except OSError:
            continue
    found.sort(reverse=True)
    return [path for _mtime, path in found]


def _sync_pinned_admin_password(conn):
    """Keep the admin password equal to SANADEX_ADMIN_PASSWORD when it is set.

    Hosting panels cannot always run a terminal, so this is the supported way to
    choose (and keep) the administrator password across deploys. While the
    variable is set it wins; remove it to change the password from the UI.
    """
    pinned = os.environ.get('SANADEX_ADMIN_PASSWORD')
    if not pinned or len(pinned) < 6 or DIALECT != 'sqlite':
        return
    from framework import check_password, hash_password
    admin = one(conn, "SELECT * FROM users WHERE username='admin'")
    if not admin:
        return
    if not check_password(pinned, admin['password']):
        conn.execute('UPDATE users SET password=?, active=1, is_admin=1 WHERE id=?',
                     (hash_password(pinned), admin['id']))
        conn.commit()
        print('  Admin password set from SANADEX_ADMIN_PASSWORD')
    else:
        print('  Admin password is pinned by SANADEX_ADMIN_PASSWORD '
              '(remove the variable to change it from the UI)')


def default_role_id(conn):
    """Return (creating when needed) the built-in role for new accounts.

    Self-registered accounts get every operational permission but never the
    administrator flag, so the licence generator and user management stay
    owner-only. The role can still be edited from Roles afterwards.
    """
    meta = one(conn, "SELECT v FROM app_meta WHERE k='default_role_id'")
    if meta:
        try:
            rid = int(meta['v'])
        except (TypeError, ValueError):
            rid = 0
        if rid and one(conn, 'SELECT id FROM roles WHERE id=?', (rid,)):
            return rid
    row = one(conn, "SELECT id FROM roles WHERE is_system=1 AND name='Staff' ORDER BY id")
    if row:
        rid = row['id']
    else:
        cur = conn.execute(
            'INSERT INTO roles(name,notes,is_system,p_sell,p_buy,p_inv,p_treasury,p_reports,'
            'p_settings,p_hr,p_acc,p_services,p_inst) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('Staff', 'Default role for new accounts', 1,
             1, 1, 1, 1, 1, 1, 1, 1, 1, 1))
        conn.commit()
        rid = cur.lastrowid
    cur = conn.execute("UPDATE app_meta SET v=? WHERE k='default_role_id'", (str(rid),))
    if not cur.rowcount:
        conn.execute("INSERT INTO app_meta(k,v) VALUES('default_role_id',?)", (str(rid),))
    conn.commit()
    return rid


def seed(conn):
    """Insert sensible defaults for a brand new database (idempotent)."""
    from framework import hash_password
    if not one(conn, 'SELECT id FROM company WHERE id=1'):
        conn.execute("INSERT INTO company(id,name,phone,address,currency,lang) "
                     "VALUES(1,'','','','','ar')")
    if not one(conn, 'SELECT id FROM users LIMIT 1'):
        password, generated = initial_admin_password()
        conn.execute('INSERT INTO users(username,password,full_name,is_admin,active,created_at) '
                     'VALUES(?,?,?,?,?,?)',
                     ('admin', hash_password(password), 'Administrator', 1, 1, time.time()))
        _announce_initial_password(password, generated)
    if not one(conn, 'SELECT id FROM accounts_chart LIMIT 1'):
        for row in CHART_SEED:
            conn.execute('INSERT INTO accounts_chart(code,name,name_en,parent_id,atype,is_group) '
                         'VALUES(?,?,?,?,?,?)', row)
    if not one(conn, 'SELECT id FROM price_tiers LIMIT 1'):
        for name, pct in (('Retail', 0), ('Semi wholesale', 5), ('Wholesale', 10)):
            conn.execute('INSERT INTO price_tiers(name,percent) VALUES(?,?)', (name, pct))
    if not one(conn, 'SELECT id FROM stores LIMIT 1'):
        conn.execute("INSERT INTO stores(name,location,active) VALUES('Main store','',1)")
    if not one(conn, 'SELECT id FROM treasuries LIMIT 1'):
        conn.execute("INSERT INTO treasuries(name,ttype,balance,active) VALUES('Main cash','cash',0,1)")
    if not one(conn, 'SELECT id FROM job_roles LIMIT 1'):
        conn.execute("INSERT INTO job_roles(name) VALUES('Staff')")
    if not one(conn, 'SELECT id FROM salary_packages LIMIT 1'):
        conn.execute("INSERT INTO salary_packages(name,basic,notes) VALUES('Standard package',0,'')")
    if not one(conn, 'SELECT id FROM roles LIMIT 1'):
        conn.execute('INSERT INTO roles(name,notes,is_system,p_sell,p_buy,p_inv,p_treasury,p_reports,'
                     'p_settings,p_hr,p_acc,p_services,p_inst) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('Administrator', 'Full access', 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1))
    default_role_id(conn)
    if not one(conn, 'SELECT id FROM app_meta WHERE k=?', ('schema_version',)):
        conn.execute('INSERT INTO app_meta(k,v) VALUES(?,?)', ('schema_version', str(SCHEMA_VERSION)))
    if not one(conn, 'SELECT id FROM app_meta WHERE k=?', ('installed_at',)):
        conn.execute('INSERT INTO app_meta(k,v) VALUES(?,?)', ('installed_at', str(time.time())))
    conn.commit()


def _adopt_legacy_database():
    """First run after the rebrand: keep the user's existing data.

    The old file is renamed afterwards, so replacing the application folder can
    never silently import the stale (often empty) database again — which used to
    recreate the default administrator after every deploy.
    """
    if DIALECT != 'sqlite':
        return False
    if os.path.exists(DB_PATH):
        # The live database is already established: retire the legacy file so a
        # later redeploy that replaces the folder can never re-import it (which
        # used to recreate the default administrator after every deploy).
        if os.path.exists(LEGACY_DB):
            try:
                os.replace(LEGACY_DB, LEGACY_DB + '.migrated')
                print(f'  Legacy database retired -> {os.path.basename(LEGACY_DB)}.migrated')
            except OSError:
                pass
        return False
    if not os.path.exists(LEGACY_DB):
        return False
    if _existing_backups():
        return False
    try:
        shutil.copy2(LEGACY_DB, DB_PATH)
        for suffix in ('', '-journal', '-wal', '-shm'):
            source = LEGACY_DB + suffix
            if os.path.exists(source):
                try:
                    os.replace(source, source + '.migrated')
                except OSError:
                    pass
        print(f'  Adopted legacy database {os.path.basename(LEGACY_DB)} -> {os.path.basename(DB_PATH)}')
        return True
    except OSError:
        return False


def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if DIALECT == 'sqlite' and not os.path.exists(DB_PATH) \
            and os.environ.get('SANADEX_FRESH') != '1':
        backups = _existing_backups()
        if backups:
            raise RuntimeError(
                'The database file was not found: ' + DB_PATH + '\n'
                f'But {len(backups)} backup(s) exist in {BACKUP_DIR}.\n'
                'Refusing to start with an empty database, because that would reset your '
                'administrator password and hide all data.\n'
                'Restore one of them first:\n'
                f'    python manage.py restore "{backups[0]}"\n'
                'or, only if you really want an empty installation:\n'
                '    set SANADEX_FRESH=1   (then start the app once)')
    _adopt_legacy_database()
    conn = connect()
    try:
        conn.executescript(create_ddl(conn.dialect))
        conn.commit()
        for name, table, cols, uniq in INDEXES:
            try:
                conn.execute(index_ddl(conn.dialect, name, table, cols, uniq))
                conn.commit()
            except Exception:
                conn.rollback()
        migrate(conn)
        seed(conn)
        _sync_pinned_admin_password(conn)
    finally:
        conn.close()
    return True


# --------------------------------------------------------------------------- #
# backups
# --------------------------------------------------------------------------- #
def backup_files():
    if not os.path.isdir(BACKUP_DIR):
        return []
    rows = []
    for name in os.listdir(BACKUP_DIR):
        if not name.lower().endswith('.db'):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        rows.append({'name': name, 'size': st.st_size, 'mtime': st.st_mtime})
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    return rows


def backup_now(upload_to_drive=False):
    """Create a consistent backup. Returns dict with path/name/bytes/drive info."""
    init_db()
    ts = time.strftime('%Y%m%d-%H%M%S')
    name = f'sanadex-backup-{ts}.db'
    dest = os.path.join(BACKUP_DIR, name)
    if DIALECT == 'sqlite':
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    else:
        raise RuntimeError('Automatic file backup is only supported for the SQLite engine. '
                           'Use SQL Server maintenance plans for the mssql engine.')
    conn = connect()
    try:
        ex(conn, 'UPDATE company SET last_backup=? WHERE id=1', (time.time(),))
    finally:
        conn.close()
    # prune
    files = backup_files()
    for old in files[KEEP_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old['name']))
        except OSError:
            pass
    result = {'name': name, 'path': dest, 'bytes': os.path.getsize(dest), 'drive': None}
    if upload_to_drive:
        try:
            import gdrive
            result['drive'] = gdrive.upload(dest, name)
        except Exception as exc:  # pragma: no cover - network dependent
            result['drive'] = {'ok': False, 'error': str(exc)}
    return result


def maybe_auto_backup(conn):
    """Run the 24h (configurable) automatic backup when it is due."""
    try:
        comp = one(conn, 'SELECT auto_backup, backup_hours, last_backup, gdrive_enabled FROM company WHERE id=1') or {}
    except Exception:
        return None
    if not comp.get('auto_backup'):
        return None
    hours = comp.get('backup_hours') or 24
    if time.time() - (comp.get('last_backup') or 0) < hours * 3600:
        return None
    try:
        return backup_now(upload_to_drive=bool(comp.get('gdrive_enabled')))
    except Exception:
        return None


def restore_backup(path):
    """Restore a validated backup into the live database.

    Uses SQLite's own backup API instead of copying files: it rewrites every
    page through the engine, so a pending WAL cannot shadow the restored data
    (file-level copies + left-over -wal files are the classic way to "restore"
    and still see the old rows on Windows).
    """
    if DIALECT != 'sqlite':
        raise RuntimeError('Restore is only supported for the SQLite engine.')
    if os.path.abspath(path) == os.path.abspath(DB_PATH):
        return True
    source = sqlite3.connect(path)
    try:
        source.execute('SELECT id FROM company LIMIT 1').fetchone()
        destination = sqlite3.connect(DB_PATH, timeout=20)
        try:
            source.backup(destination)
            try:
                destination.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except Exception:
                pass
            destination.commit()
        finally:
            destination.close()
    finally:
        source.close()
    verify = sqlite3.connect(DB_PATH, timeout=20)
    try:
        verify.execute('SELECT id FROM company LIMIT 1').fetchone()
    finally:
        verify.close()
    return True


