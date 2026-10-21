# -*- coding: utf-8 -*-
"""Dev helper: hit every registered GET route and report non-200 responses."""
import io
import os
import re
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

TEST_DB = os.path.join(BASE, 'smoke.db')
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(TEST_DB + suffix)
    except OSError:
        pass
os.environ.setdefault('SANADEX_ADMIN_PASSWORD', 'admin123')
os.environ.setdefault('SANADEX_FRESH', '1')
import db  # noqa: E402
db.DB_PATH = TEST_DB
_SMOKE_BACKUPS = os.path.join(BASE, 'smoke-backups')
import shutil as _shutil  # noqa: E402
_shutil.rmtree(_SMOKE_BACKUPS, ignore_errors=True)
os.makedirs(_SMOKE_BACKUPS, exist_ok=True)
db.BACKUP_DIR = _SMOKE_BACKUPS
db.init_db()
import app  # noqa: E402

STATUS = []


def call(path, method='GET', data=None, cookie=''):
    body = b''
    if data is not None:
        body = urllib.parse.urlencode(data, doseq=True).encode()
    environ = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path.split('?')[0],
        'QUERY_STRING': path.split('?')[1] if '?' in path else '',
        'wsgi.input': io.BytesIO(body),
        'CONTENT_LENGTH': str(len(body)),
        'CONTENT_TYPE': 'application/x-www-form-urlencoded',
        'HTTP_COOKIE': cookie,
        'REMOTE_ADDR': '127.0.0.1',
        'HTTP_HOST': 'localhost:8000',
    }
    box = {}

    def start_response(status, headers):
        box['status'] = status
        box['headers'] = dict(headers)

    payload = b''.join(app.application(environ, start_response))
    return box['status'], box['headers'], payload.decode('utf-8', 'replace')


status, headers, body = call('/login', 'POST', {'username': 'admin', 'password': 'admin123'})
cookie = headers.get('Set-Cookie', '').split(';')[0]
if not status.startswith('302'):
    print('LOGIN FAILED', status, body[:500])
    sys.exit(1)

skip = {'login', 'logout', 'set_lang', 'set_theme', 'backup_download', 'gdrive_callback',
        'invoice_print', 'invoice_view', 'invoice_cancel', 'invoice_pay', 'invoice_duplicate',
        'item_edit', 'order_print', 'order_convert', 'order_done', 'order_delete',
        'transfer_print', 'transfer_delete', 'voucher_print', 'voucher_edit', 'voucher_delete',
        'journal_view', 'journal_delete', 'installment_pay', 'installment_plan_view',
        'payroll_view', 'payslip', 'service_collect', 'cheque_clear', 'cheque_bounce',
        'backup_now', 'backup_delete', 'backup_restore_do', 'gdrive_connect', 'gdrive_disconnect',
        'gdrive_upload', 'gdrive_restore', 'demo_load', 'demo_clear', 'api_search', 'item_units_json',
        'crud_edit', 'set_lang', 'sale_invoice_new', 'purchase_invoice_new', 'order_new',
        'item_import', 'barcode', 'transfer_new', 'voucher_new', 'journal_new',
        'license_download', 'license_generate', 'license_activate', 'license_revoke',
        'license_user_action', 'signup'}

problems = []
for name, methods, regex, pattern, fn in app.router.routes:
    if name in skip or 'POST' not in methods and 'GET' not in methods:
        continue
    if '<' in pattern:
        continue
    path = pattern
    status, headers, body = call(path, 'GET', cookie=cookie)
    marker = 'OK' if status.startswith('200') else status
    print(f'{marker:<24} {path}')
    if not status.startswith('200'):
        problems.append((path, status, body[:600]))

print()
if problems:
    print(f'{len(problems)} problem routes')
    for path, status, body in problems:
        snippet = re.sub(r'\s+', ' ', body)[:400]
        print(f'\n=== {path} -> {status}\n{snippet}')
    sys.exit(1)
print('all routes OK')
try:
    os.remove(TEST_DB)
except OSError:
    pass
_shutil.rmtree(_SMOKE_BACKUPS, ignore_errors=True)
