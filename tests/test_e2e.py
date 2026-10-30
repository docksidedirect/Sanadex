# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - end-to-end acceptance suite.

Runs the real WSGI application against a throw-away database and checks every
module end to end: money, stock, permissions, bilingual UI, Excel bridge,
barcodes, backups and reports.
"""
import io
import json
import os
import re
import sys
import urllib.parse
import hashlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

TEST_DB = os.path.join(BASE, 'test-sanadex.db')
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(TEST_DB + suffix)
    except OSError:
        pass

os.environ.setdefault('SANADEX_ADMIN_PASSWORD', 'admin123')
os.environ.setdefault('SANADEX_FRESH', '1')

import db  # noqa: E402
db.DB_PATH = TEST_DB
TEST_BACKUPS = os.path.join(BASE, 'test-backups')
TEST_UPLOADS = os.path.join(BASE, 'test-uploads')
import shutil as _shutil  # noqa: E402
for folder in (TEST_BACKUPS, TEST_UPLOADS):
    _shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
db.BACKUP_DIR = TEST_BACKUPS
db.UPLOAD_DIR = TEST_UPLOADS
db.init_db()
os.environ.pop('SANADEX_ADMIN_PASSWORD', None)  # only pins the very first seed

import app  # noqa: E402
import barcode as barcode_lib  # noqa: E402
import i18n  # noqa: E402
import xlsx  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, extra=''):
    if condition:
        PASSED.append(name)
        print(f'  PASS  {name}')
    else:
        FAILED.append((name, extra))
        print(f'  FAIL  {name}   {str(extra)[:220]}')


def section(title):
    print(f'\n--- {title} ' + '-' * max(0, 58 - len(title)))


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self):
        self.cookie = ''
        self.last_headers = {}

    def request(self, path, method='GET', data=None, body=None, content_type=None, extra=None):
        if body is None:
            body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else b''
        environ = {
            'REQUEST_METHOD': method,
            'PATH_INFO': path.split('?')[0],
            'QUERY_STRING': path.split('?')[1] if '?' in path else '',
            'wsgi.input': io.BytesIO(body),
            'CONTENT_LENGTH': str(len(body)),
            'CONTENT_TYPE': content_type or 'application/x-www-form-urlencoded',
            'HTTP_COOKIE': self.cookie,
            'REMOTE_ADDR': '127.0.0.1',
            'HTTP_HOST': 'localhost:8000',
        }
        if extra:
            environ.update(extra)
        box = {}

        def start_response(status, headers):
            box['status'] = status
            box['headers'] = headers

        payload = b''.join(app.application(environ, start_response))
        self.last_headers = dict(box['headers'])
        for header, value in box['headers']:
            if header.lower() == 'set-cookie':
                self.cookie = value.split(';')[0]
        return box['status'], self.last_headers, payload

    def get(self, path):
        status, headers, body = self.request(path)
        return status, headers, body.decode('utf-8', 'replace')

    def post(self, path, data=None, files=None):
        if files:
            body, ctype = multipart_body(data or {}, files)
            status, headers, payload = self.request(path, 'POST', body=body, content_type=ctype)
        else:
            status, headers, payload = self.request(path, 'POST', data=data or {})
        return status, headers, payload.decode('utf-8', 'replace')

    def json(self, path):
        status, headers, body = self.get(path)
        try:
            return json.loads(body)
        except ValueError:
            return {}


def multipart_body(fields, files):
    boundary = '----SanadexTestBoundary'
    out = b''
    for key, value in fields.items():
        for item in (value if isinstance(value, list) else [value]):
            out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
                    .encode() + str(item).encode() + b'\r\n')
    for key, (filename, data, ctype) in files.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n').encode()
        out += data + b'\r\n'
    out += f'--{boundary}--\r\n'.encode()
    return out, f'multipart/form-data; boundary={boundary}'


def conn():
    return db.connect()


def row(sql, args=()):
    c = conn()
    try:
        return db.one(c, sql, args)
    finally:
        c.close()


def rows(sql, args=()):
    c = conn()
    try:
        return db.q(c, sql, args)
    finally:
        c.close()


def value(sql, args=(), default=0):
    c = conn()
    try:
        return db.scalar(c, sql, args, default)
    finally:
        c.close()


# --------------------------------------------------------------------------- #
section('boot, brand and bilingual shell')
# --------------------------------------------------------------------------- #
admin = Client()
status, headers, body = admin.post('/login', {'username': 'admin', 'password': 'admin123'})
check('admin can sign in', status.startswith('302'), status)
guest = Client()
check('login page carries the new brand', 'Sanadex.wanis.online' in guest.get('/login')[2])
status, headers, body = guest.get('/login')
check('security headers are present',
      headers.get('X-Content-Type-Options') == 'nosniff' and
      headers.get('X-Frame-Options') == 'SAMEORIGIN' and
      'frame-ancestors' in headers.get('Content-Security-Policy', ''))
check('HTTP cookie is not marked Secure', 'Secure' not in headers.get('Set-Cookie', ''))
https_client = Client()
status, headers, body = https_client.request('/login', extra={'HTTP_X_FORWARDED_PROTO': 'https'})
check('HTTPS cookie is marked Secure', 'Secure' in headers.get('Set-Cookie', ''), headers.get('Set-Cookie'))
check('HTTPS sends HSTS', headers.get('Strict-Transport-Security', '').startswith('max-age='),
      headers.get('Strict-Transport-Security'))
os.environ['SANADEX_FORCE_HTTPS'] = '1'
try:
    status, headers, body = Client().request('/login')
finally:
    os.environ.pop('SANADEX_FORCE_HTTPS', None)
check('forced HTTPS sets Secure cookie and HSTS',
      'Secure' in headers.get('Set-Cookie', '') and
      headers.get('Strict-Transport-Security', '').startswith('max-age='))

csrf_client = Client()
status, headers, body = csrf_client.request(
    '/login', 'POST', {'username': 'admin', 'password': 'admin123'},
    extra={'HTTP_ORIGIN': 'https://evil.example'})
check('cross-origin POST is blocked', status.startswith('403'), status)
status, headers, body = Client().request(
    '/login', 'POST', {'username': 'admin', 'password': 'admin123'},
    extra={'HTTP_ORIGIN': 'http://localhost:8000'})
check('same-origin POST is allowed', status.startswith('302'), status)

throttle_client = Client()
for _attempt in range(8):
    throttle_client.request('/login', 'POST', {'username': 'lockme', 'password': 'bad'},
                            extra={'HTTP_X_FORWARDED_FOR': '203.0.113.77'})
status, headers, body = throttle_client.request(
    '/login', 'POST', {'username': 'lockme', 'password': 'bad'},
    extra={'HTTP_X_FORWARDED_FOR': '203.0.113.77'})
check('repeated login failures are throttled',
      status.startswith('429') and 'محاولات كثيرة' in body.decode('utf-8', 'replace'), status)

svg_path = os.path.join(db.UPLOAD_DIR, 'security-test.svg')
with open(svg_path, 'w', encoding='utf-8') as handle:
    handle.write('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
status, headers, body = Client().get('/uploads/security-test.svg')
check('uploaded SVG is sandboxed',
      status.startswith('200') and 'sandbox' in headers.get('Content-Security-Policy', '') and
      headers.get('X-Content-Type-Options') == 'nosniff', headers.get('Content-Security-Policy'))

legacy_salt = os.urandom(16).hex()
legacy_hash = legacy_salt + '$' + hashlib.pbkdf2_hmac(
    'sha256', b'Legacy123', bytes.fromhex(legacy_salt), 60000).hex()
legacy_conn = conn()
db.x(legacy_conn, 'INSERT INTO users(username,password,full_name,role_id,is_admin,active) '
                  'VALUES(?,?,?,?,?,?)',
     ('legacy_login_test', legacy_hash, 'Legacy Test', 0, 0, 1))
legacy_conn.close()
legacy_client = Client()
status, headers, body = legacy_client.post('/login', {'username': 'legacy_login_test',
                                                      'password': 'Legacy123'})
check('legacy password login still works', status.startswith('302'), status)
stored_hash = value("SELECT password FROM users WHERE username='legacy_login_test'")
check('legacy password hash is upgraded on login',
      str(stored_hash).startswith('pbkdf2_sha256$600000$'), stored_hash)
cleanup_conn = conn()
db.ex(cleanup_conn, "DELETE FROM users WHERE username='legacy_login_test'")
cleanup_conn.close()

status, headers, body = admin.get('/')
check('dashboard renders', status.startswith('200') and 'Sanadex.wanis.online' in body, status)
check('dashboard shows KPI cards', 'kpi-value' in body)

admin.get('/lang/en')
status, headers, body = admin.get('/')
check('English UI translates server-side',
      'Sales today' in body and 'مبيعات اليوم' not in body, status)
admin.get('/lang/ar')
status, headers, body = admin.get('/')
check('Arabic UI returns', 'مبيعات اليوم' in body and 'dir="rtl"' in body)

admin.get('/theme/dark')
status, headers, body = admin.get('/')
check('dark theme applied', 'data-theme="dark"' in body)
admin.get('/theme/light')

missing_keys = []
for name in os.listdir(os.path.join(BASE, 'templates')):
    if not name.endswith('.html'):
        continue
    text = open(os.path.join(BASE, 'templates', name), encoding='utf-8').read()
    for match in re.finditer(r"\bt\('([\w.]+)'", text):
        key = match.group(1)
        if key.endswith('_'):
            continue  # dynamic prefix such as status_<value>
        if not i18n.has(key):
            missing_keys.append(key)
check('all template i18n keys exist', not missing_keys, missing_keys[:8])

legacy = []
for name in os.listdir(BASE):
    if name.endswith(('.py', '.md', '.bat', '.sh')):
        text = open(os.path.join(BASE, name), encoding='utf-8', errors='replace').read()
        if 'Sahm' in text or 'SAHM' in text:
            legacy.append(name)
check('no legacy "Sahm" brand left in code', not legacy, legacy)

# --------------------------------------------------------------------------- #
section('settings and organisation profile')
# --------------------------------------------------------------------------- #
status, headers, body = admin.post('/settings', {
    'name': 'Wanis Trading', 'phone': '0100', 'address': 'Cairo', 'email': 'hi@example.com',
    'tax_number': 'TAX-1', 'cr_number': 'CR-9', 'currency': 'EGP', 'company_type': 'retail',
    'lang': 'ar', 'theme': 'light', 'accent': 'clay', 'inv_prefix': 'INV-', 'inv_suffix': '',
    'pinv_prefix': 'PUR-', 'pinv_suffix': '', 'order_prefix': 'SO-', 'vch_prefix': 'VCH-',
    'next_sale_no': '1', 'next_purchase_no': '1', 'next_order_no': '1', 'next_voucher_no': '1',
    'tax_percent': '14', 'expiry_alert_days': '30', 'backup_hours': '24',
    'restrict_scope': 'on', 'show_cost': 'on', 'invoice_terms': 'Goods are not returnable.',
    'receipt_footer': 'Thank you',
})
company = row('SELECT * FROM company WHERE id=1')
check('company profile saved', company['name'] == 'Wanis Trading' and company['currency'] == 'EGP',
      company['name'])
check('scope restriction flag stored', company['restrict_scope'] == 1)

tiny_png = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
            b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00'
            b'\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
status, headers, body = admin.post('/settings', {
    'name': 'Wanis Trading', 'currency': 'EGP', 'tax_percent': '14', 'expiry_alert_days': '30',
    'backup_hours': '24', 'next_sale_no': '1', 'next_purchase_no': '1', 'lang': 'ar',
    'theme': 'light', 'accent': 'clay', 'restrict_scope': 'on', 'show_cost': 'on',
}, files={'logo': ('logo.png', tiny_png, 'image/png')})
logo = row('SELECT logo FROM company WHERE id=1')['logo']
check('logo uploaded', bool(logo) and os.path.isfile(os.path.join(db.UPLOAD_DIR, logo)), logo)
status, headers, body = admin.get('/uploads/' + (logo or 'x'))
check('uploaded logo is served', status.startswith('200'), status)

# restore the full profile (the logo post only carried a subset of the fields)
admin.post('/settings', {
    'name': 'Wanis Trading', 'phone': '0100', 'address': 'Cairo', 'email': 'hi@example.com',
    'tax_number': 'TAX-1', 'cr_number': 'CR-9', 'currency': 'EGP', 'company_type': 'retail',
    'lang': 'ar', 'theme': 'light', 'accent': 'clay', 'inv_prefix': 'INV-', 'inv_suffix': '',
    'pinv_prefix': 'PUR-', 'pinv_suffix': '', 'order_prefix': 'SO-', 'vch_prefix': 'VCH-',
    'next_sale_no': '1', 'next_purchase_no': '1', 'next_order_no': '1', 'next_voucher_no': '1',
    'tax_percent': '14', 'expiry_alert_days': '30', 'backup_hours': '24',
    'restrict_scope': 'on', 'show_cost': 'on', 'invoice_terms': 'Goods are not returnable.',
    'receipt_footer': 'Thank you',
})

# --------------------------------------------------------------------------- #
section('master data: stores, treasuries, tiers, parties, items')
# --------------------------------------------------------------------------- #
admin.post('/stores/new', {'name': 'Branch store', 'location': 'Giza', 'phone': '',
                           'manager': '', 'active': 'on'})
admin.post('/treasuries/new', {'name': 'Cash box', 'ttype': 'cash', 'balance': '1000',
                               'account_no': '', 'iban': '', 'bank_name': '', 'active': 'on'})
admin.post('/treasuries/new', {'name': 'Bank account', 'ttype': 'bank', 'balance': '0',
                               'account_no': '123', 'iban': '', 'bank_name': 'NBE', 'active': 'on'})
store_main = value('SELECT id FROM stores ORDER BY id LIMIT 1')
store_branch = value("SELECT id FROM stores WHERE name='Branch store'")
cash = value("SELECT id FROM treasuries WHERE name='Cash box'")
bank = value("SELECT id FROM treasuries WHERE name='Bank account'")
tier = value('SELECT id FROM price_tiers WHERE percent=10')
check('two stores exist', bool(store_main) and bool(store_branch))
check('treasuries created with opening balance', bool(cash) and bool(bank) and
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 1000)
check('price tiers seeded', bool(tier))

admin.post('/customers/new', {'name': 'Acme Retail', 'phone': '0100000', 'email': '',
                              'address': 'Cairo', 'tax_number': '', 'tier_id': str(tier),
                              'credit_limit': '0', 'opening_balance': '0', 'active': 'on',
                              'notes': ''})
admin.post('/suppliers/new', {'name': 'Delta Supplier', 'phone': '0111111', 'email': '',
                              'address': 'Alex', 'tax_number': '', 'opening_balance': '0',
                              'active': 'on', 'notes': ''})
customer = value("SELECT id FROM customers WHERE name='Acme Retail'")
supplier = value("SELECT id FROM suppliers WHERE name='Delta Supplier'")
check('customer and supplier created', bool(customer) and bool(supplier))

admin.post('/items/new', {
    'code': 'P1', 'name': 'Test product', 'name_en': 'Test product', 'category': 'General',
    'brand': 'BrandX', 'purchase_price': '50', 'sale_price': '80', 'min_price': '0',
    'reorder_level': '5', 'has_expiry': 'on', 'active': 'on', 'notes': '',
    'u_name': ['Carton', 'Piece'], 'u_factor': ['12', '1'], 'u_barcode': ['111', '222'],
    'u_sprice': ['900', '80'], 'u_pprice': ['600', '50'],
})
item = value("SELECT id FROM items WHERE code='P1'")
units = rows('SELECT * FROM item_units WHERE item_id=? ORDER BY factor DESC', (item,))
check('item with two units stored', len(units) == 2, len(units))
carton = next((u['id'] for u in units if u['name'] == 'Carton'), 0)
piece = next((u['id'] for u in units if u['name'] == 'Piece'), 0)
check('carton barcode and price saved', bool(carton) and
      row('SELECT barcode FROM item_units WHERE id=?', (carton,))['barcode'] == '111')

admin.post('/items/new', {'code': 'P2', 'name': 'Low stock item', 'category': 'General',
                          'purchase_price': '10', 'sale_price': '20', 'reorder_level': '100',
                          'active': 'on', 'u_name': ['Piece'], 'u_factor': ['1'],
                          'u_barcode': [''], 'u_sprice': ['20'], 'u_pprice': ['10']})
check('second item created', value("SELECT COUNT(*) c FROM items WHERE code='P2'") == 1)

# --------------------------------------------------------------------------- #
section('purchase invoice: stock, batch, supplier, treasury')
# --------------------------------------------------------------------------- #
status, headers, body = admin.post('/invoice/purchase/new', {
    'party_id': str(supplier), 'store_id': str(store_main), 'treasury_id': str(cash),
    'idate': db.today(), 'due_date': '', 'discount': '0', 'paid': '500', 'notes': 'first buy',
    'ref': 'PO-1', 'item_id': [str(item)], 'unit_id': [str(carton)], 'qty': ['2'],
    'price': ['900'], 'expiry': ['2027-01-01'],
})
purchase = row("SELECT * FROM invoices WHERE itype='purchase'")
check('purchase invoice saved', status.startswith('302') and purchase is not None, status)
check('purchase subtotal = 2 x 900', float(purchase['subtotal']) == 1800.0, purchase['subtotal'])
check('purchase is partially paid', purchase['status'] == 'partial', purchase['status'])
check('supplier owes the unpaid part',
      row('SELECT balance FROM suppliers WHERE id=?', (supplier,))['balance'] == 1300)
check('treasury paid the supplier',
      row('SELECT balance FROM treasuries WHERE id=?', (cash,))['balance'] == 500)
check('stock in base units = 24',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 24)
batch = row('SELECT * FROM item_batches WHERE item_id=?', (item,))
check('expiry batch created with the right date',
      batch and batch['qty'] == 24 and batch['expiry_date'] == '2027-01-01')
check('item master cost updated from purchase (900/12)',
      row('SELECT purchase_price FROM items WHERE id=?', (item,))['purchase_price'] == 75.0,
      row('SELECT purchase_price FROM items WHERE id=?', (item,)))

# --------------------------------------------------------------------------- #
section('sales invoice: tier pricing, tax, partial payment, cancel')
# --------------------------------------------------------------------------- #
status, headers, body = admin.post('/invoice/sale/new', {
    'party_id': str(customer), 'store_id': str(store_main), 'treasury_id': str(cash),
    'idate': db.today(), 'due_date': '', 'discount': '50', 'paid': '500', 'is_tax': 'on',
    'tax_percent': '14', 'notes': '', 'ref': '',
    'item_id': [str(item)], 'unit_id': [str(piece)], 'qty': ['10'], 'price': [''],
})
sale1 = row("SELECT * FROM invoices WHERE itype='sale' ORDER BY id LIMIT 1")
line = row('SELECT * FROM invoice_items WHERE invoice_id=?', (sale1['id'],))
check('tier discount applied to the sale price (80 -> 72)',
      round(float(line['price']), 2) == 72.0, line['price'])
check('sale subtotal 720', float(sale1['subtotal']) == 720.0, sale1['subtotal'])
check('invoice discount and tax computed',
      float(sale1['net']) == round((720 - 50) * 1.14, 2), sale1['net'])
check('partial payment status and customer debt',
      sale1['status'] == 'partial' and
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) ==
      round(float(sale1['net']) - 500, 2))
check('stock dropped to 14',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 14)
check('batch consumed FIFO to 14',
      row('SELECT qty FROM item_batches WHERE id=?', (batch['id'],))['qty'] == 14)

status, headers, body = admin.get(f"/invoice/{sale1['id']}")
check('invoice detail page renders',
      status.startswith('200') and str(sale1['number']) in body, status)
status, headers, body = admin.get(f"/invoice/{sale1['id']}/print")
check('invoice print page renders', status.startswith('200') and 'items-table' in body, status)

admin.post(f"/invoice/{sale1['id']}/cancel")
check('cancel restores stock',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 24)
check('cancel restores customer balance',
      row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'] == 0)
check('cancel restores treasury',
      row('SELECT balance FROM treasuries WHERE id=?', (cash,))['balance'] == 500)
check('cancelled invoice marked', row('SELECT status FROM invoices WHERE id=?',
                                      (sale1['id'],))['status'] == 'cancelled')

# --------------------------------------------------------------------------- #
section('offers, credit sale, collecting a payment')
# --------------------------------------------------------------------------- #
admin.post('/offers/new', {'name': 'Launch 10%', 'scope': 'item', 'item_id': str(item),
                           'discount_percent': '10', 'start_date': '2020-01-01',
                           'end_date': '2030-12-31', 'active': 'on'})
admin.post('/invoice/sale/new', {
    'party_id': str(customer), 'store_id': str(store_main), 'treasury_id': str(cash),
    'idate': db.today(), 'discount': '0', 'paid': '0', 'notes': '', 'ref': '',
    'item_id': [str(item)], 'unit_id': [str(piece)], 'qty': ['2'], 'price': [''],
})
sale2 = row("SELECT * FROM invoices WHERE itype='sale' AND status='credit'")
line2 = row('SELECT * FROM invoice_items WHERE invoice_id=?', (sale2['id'],))
check('tier + offer stacked (80 -> 72 -> 64.8)',
      round(float(line2['price']), 2) == 64.8, line2['price'])
check('credit sale debt recorded',
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == 129.6)

status, headers, body = admin.post(f"/invoice/{sale2['id']}/pay",
                                   {'amount': '100', 'treasury_id': str(cash), 'vdate': db.today()})
check('payment creates a voucher', value('SELECT COUNT(*) c FROM vouchers') >= 1)
check('payment updates the invoice',
      row('SELECT paid, status FROM invoices WHERE id=?', (sale2['id'],))['status'] == 'partial')
check('payment updates the customer balance',
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == 29.6)
check('payment updates the treasury',
      row('SELECT balance FROM treasuries WHERE id=?', (cash,))['balance'] == 600)

admin.post('/invoice/sale/new', {
    'party_id': '', 'store_id': str(store_main), 'treasury_id': str(cash),
    'idate': db.today(), 'discount': '0', 'paid': '0', 'is_tax': 'on', 'tax_percent': '14',
    'notes': '', 'ref': '', 'item_id': [str(item)], 'unit_id': [str(piece)], 'qty': ['1'],
    'price': [''],
})
sale3 = row('SELECT * FROM invoices ORDER BY id DESC LIMIT 1')
check('walk-in tax invoice computed', round(float(sale3['net']), 2) == round(72 * 1.14, 2),
      sale3['net'])

# --------------------------------------------------------------------------- #
section('store transfers, damages, withdrawals, treasury transfers')
# --------------------------------------------------------------------------- #
status, headers, body = admin.post('/transfers/new', {
    'from_store': str(store_main), 'to_store': str(store_branch), 'tdate': db.today(),
    'note': 'move', 'item_id': [str(item)], 'unit_id': [str(piece)], 'qty': ['4'],
})
check('transfer created', value('SELECT COUNT(*) c FROM transfers') == 1)
check('transfer moved stock between stores',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=? AND store_id=?',
            (item, store_main)) == 17 and
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=? AND store_id=?',
            (item, store_branch)) == 4)
status, headers, body = admin.get('/transfers/1/print')
check('transfer note prints', status.startswith('200') and 'items-table' in body, status)

admin.post('/damages/new', {'item_id': str(item), 'store_id': str(store_main), 'qty': '1',
                            'unit_id': str(piece), 'reason': 'broken', 'ddate': db.today(),
                            'note': ''})
damage = value('SELECT MAX(id) v FROM damages')
check('damage reduces stock',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 20)
admin.post(f'/damages/{damage}/edit', {'item_id': str(item), 'store_id': str(store_main),
                                       'qty': '2', 'unit_id': str(piece), 'reason': 'broken',
                                       'ddate': db.today(), 'note': ''})
check('editing a damage reverses the old quantity',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 19)
admin.post(f'/damages/{damage}/delete')
check('deleting a damage restores stock',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 21)

admin.post('/withdrawals/new', {'treasury_id': str(cash), 'amount': '50', 'wtype': 'personal',
                                'wdate': db.today(), 'note': 'owner'})
withdrawal = value('SELECT MAX(id) v FROM withdrawals')
check('withdrawal reduces the treasury',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 550)
admin.post(f'/withdrawals/{withdrawal}/edit', {'treasury_id': str(cash), 'amount': '80',
                                                'wtype': 'personal', 'wdate': db.today(),
                                                'note': ''})
check('editing a withdrawal re-applies the delta',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 520)
admin.post(f'/withdrawals/{withdrawal}/delete')
check('deleting a withdrawal restores the treasury',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 600)

admin.post('/treasury_transfers/new', {'tfrom': str(cash), 'tto': str(bank), 'amount': '100',
                                       'tdate': db.today(), 'note': ''})
transfer = value('SELECT MAX(id) v FROM treasury_transfers')
check('money transfer moves cash',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 500 and
      value('SELECT balance FROM treasuries WHERE id=?', (bank,)) == 100)
admin.post(f'/treasury_transfers/{transfer}/edit', {'tfrom': str(cash), 'tto': str(bank),
                                                     'amount': '250', 'tdate': db.today(),
                                                     'note': ''})
check('editing a money transfer reverses then re-applies',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 350 and
      value('SELECT balance FROM treasuries WHERE id=?', (bank,)) == 250)
admin.post(f'/treasury_transfers/{transfer}/delete')
check('deleting a money transfer restores both',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 600 and
      value('SELECT balance FROM treasuries WHERE id=?', (bank,)) == 0)

# --------------------------------------------------------------------------- #
section('vouchers, credit notes and post-dated cheques')
# --------------------------------------------------------------------------- #
admin.post('/vouchers/new', {
    'vtype': 'receipt', 'treasury_id': str(cash), 'party_type': 'customer',
    'party_id': str(customer), 'amount': '200', 'vdate': db.today(), 'note': 'collection'})
voucher = value('SELECT MAX(id) v FROM vouchers')
check('receipt voucher increases cash and cuts debt',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 800 and
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == -170.4)
admin.post(f'/vouchers/{voucher}/edit', {
    'vtype': 'receipt', 'treasury_id': str(cash), 'party_type': 'customer',
    'party_id': str(customer), 'amount': '300', 'vdate': db.today(), 'note': 'edited'})
check('editing a voucher reverses the old amount',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 900 and
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == -270.4)
status, headers, body = admin.get(f'/vouchers/{voucher}/print')
check('voucher prints', status.startswith('200') and 'items-table' in body, status)
admin.post(f'/vouchers/{voucher}/delete')
check('deleting a voucher reverses everything',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 600 and
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == 29.6)

admin.post('/sup_credit_notes/new', {'party_type': 'supplier', 'party_id': str(supplier),
                                     'ctype': 'discount', 'amount': '100', 'cdate': db.today(),
                                     'note': ''})
note = value("SELECT id FROM credit_notes WHERE party_type='supplier' ORDER BY id DESC LIMIT 1")
check('supplier credit note reduces the payable',
      row('SELECT balance FROM suppliers WHERE id=?', (supplier,))['balance'] == 1200)
admin.post(f'/sup_credit_notes/{note}/delete')
check('deleting the note restores the payable',
      row('SELECT balance FROM suppliers WHERE id=?', (supplier,))['balance'] == 1300)

admin.post('/cheques/new', {'number': 'CH-100', 'ctype': 'in', 'party_type': 'customer',
                            'party_id': str(customer), 'bank': 'NBE', 'amount': '300',
                            'due_date': db.today(), 'treasury_id': str(cash),
                            'status': 'pending', 'note': ''})
cheque = value('SELECT MAX(id) v FROM cheques')
check('pending cheque recorded',
      row('SELECT status FROM cheques WHERE id=?', (cheque,))['status'] == 'pending')
admin.post(f'/cheques/{cheque}/clear')
check('clearing a cheque adds cash',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == 900)
check('clearing a cheque settles the party',
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'], 2) == -270.4)

# --------------------------------------------------------------------------- #
section('sale orders and conversion to an invoice')
# --------------------------------------------------------------------------- #
admin.post('/orders/new', {
    'otype': 'sale', 'party_id': str(customer), 'odate': db.today(),
    'due_date': '2020-01-01', 'notes': 'late order',
    'item_id': [str(item)], 'unit_id': [str(piece)], 'qty': ['3'], 'price': ['80']})
order = row('SELECT * FROM doc_orders ORDER BY id DESC LIMIT 1')
check('sale order stored with its number', order and order['number'].startswith('SO-'))
check('late order appears as an alert', 'متأخر' in admin.get('/')[2])
status, headers, body = admin.post(f"/orders/{order['id']}/convert")
converted = row('SELECT * FROM invoices WHERE order_id=?', (order['id'],))
check('order converted into an invoice',
      status.startswith('302') and converted and float(converted['net']) == 240.0,
      converted and converted['net'])
check('conversion moves stock',
      value('SELECT SUM(qty) v FROM stock_movements WHERE item_id=?', (item,)) == 18)
check('conversion marks the order done and linked',
      row('SELECT status FROM doc_orders WHERE id=?', (order['id'],))['status'] == 'done')

# --------------------------------------------------------------------------- #
section('installments, services, HR and payroll')
# --------------------------------------------------------------------------- #
admin.post('/installments', {
    'customer_id': str(customer), 'total': '1200', 'count': '3', 'period': 'month',
    'start_date': db.today(), 'notes': '', 'down_payment': '0'})
plan = value('SELECT MAX(id) v FROM installment_plans')
installment_rows = rows('SELECT * FROM installments WHERE plan_id=? ORDER BY due_date', (plan,))
check('installment plan created 3 rows', len(installment_rows) == 3, len(installment_rows))
check('installments sum to the total',
      round(sum(float(r['amount']) for r in installment_rows), 2) == 1200.0)
balance_before = row('SELECT balance FROM customers WHERE id=?', (customer,))['balance']
treasury_before = value('SELECT balance FROM treasuries WHERE id=?', (cash,))
admin.post(f"/installments/{installment_rows[0]['id']}/pay",
           {'amount': '400', 'treasury_id': str(cash)})
check('paying an installment takes cash and cuts debt',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == treasury_before + 400 and
      round(row('SELECT balance FROM customers WHERE id=?', (customer,))['balance'] -
            balance_before, 2) == -400)
check('installment marked paid',
      row('SELECT paid FROM installments WHERE id=?', (installment_rows[0]['id'],))['paid'] == 1)
status, headers, body = admin.get('/installments/due')
check('due installments report renders', status.startswith('200') and 'installment' in body.lower())

admin.post('/services/new', {'customer_name': 'Walk-in', 'phone': '', 'device': 'Laptop',
                             'problem': 'Screen', 'technician': 'Ali', 'cost': '300',
                             'paid': '100', 'status': 'received', 'sdate': db.today(),
                             'delivery_date': '', 'notes': ''})
service = value('SELECT MAX(id) v FROM service_orders')
treasury_before = value('SELECT balance FROM treasuries WHERE id=?', (cash,))
admin.post(f'/services/{service}/collect', {'amount': '100', 'treasury_id': str(cash)})
check('service payment reaches the treasury',
      value('SELECT balance FROM treasuries WHERE id=?', (cash,)) == treasury_before + 100)
check('service order paid amount updated',
      row('SELECT paid FROM service_orders WHERE id=?', (service,))['paid'] == 200)

admin.post('/job_roles/new', {'name': 'Technician', 'notes': ''})
job_role = value("SELECT id FROM job_roles WHERE name='Technician'")
admin.post('/employees/new', {'name': 'Sara', 'phone': '', 'email': '', 'national_id': '',
                              'job_role_id': str(job_role), 'package_id': '',
                              'salary_type': 'daily', 'salary': '0', 'daily_rate': '100',
                              'hire_date': '2025-01-01', 'active': 'on', 'notes': ''})
employee = value("SELECT id FROM employees WHERE name='Sara'")
admin.post('/attendance/new', {'employee_id': str(employee), 'adate': '2026-03-01',
                               'status': 'present', 'hours': '8', 'note': ''})
admin.post('/attendance/new', {'employee_id': str(employee), 'adate': '2026-03-02',
                               'status': 'present', 'hours': '8', 'note': ''})
admin.post('/attendance/new', {'employee_id': str(employee), 'adate': '2026-03-03',
                               'status': 'absent', 'hours': '0', 'note': ''})
check('attendance upsearch keeps one row per day',
      value('SELECT COUNT(*) c FROM attendance WHERE employee_id=?', (employee,)) == 3)
admin.post('/bonusded/new', {'employee_id': str(employee), 'ptype': 'bonus', 'amount': '50',
                             'pdate': '2026-03-05', 'note': ''})
admin.post('/advances/new', {'employee_id': str(employee), 'amount': '30',
                             'adate': '2026-03-04', 'note': ''})
admin.post('/payroll', {'month': '2026-03', 'action': 'run', 'note': ''})
payslip = row('SELECT * FROM payslips ORDER BY id DESC LIMIT 1')
check('payroll computes base + bonus - advance',
      payslip and round(float(payslip['net']), 2) == 220.0, payslip and payslip['net'])
run_id = value('SELECT MAX(id) v FROM payroll_runs')
status, headers, body = admin.get(f'/payroll/{run_id}/payslip/{employee}')
check('payslip prints', status.startswith('200') and 'items-table' in body, status)
admin.post('/holidays/new', {'name': 'Eid', 'hdate': '2026-03-20', 'htype': 'public',
                             'notes': ''})
check('holiday stored', value('SELECT COUNT(*) c FROM holidays') == 1)
admin.post('/attendance/bulk', {'adate': '2026-03-09', 'status': 'present',
                                f'emp_{employee}': '1'})
check('bulk attendance saved',
      value('SELECT COUNT(*) c FROM attendance WHERE employee_id=? AND adate=?',
            (employee, '2026-03-09')) == 1)

# --------------------------------------------------------------------------- #
section('accounting: chart, journals, statements')
# --------------------------------------------------------------------------- #
cash_account = value("SELECT id FROM accounts_chart WHERE code='1110'")
sales_account = value("SELECT id FROM accounts_chart WHERE code='4100'")
admin.post('/journals/new', {
    'jdate': db.today(), 'note': 'opening entry', 'ref': '',
    'account_id': [str(cash_account), str(sales_account)], 'debit': ['1000', '0'],
    'credit': ['0', '1000'], 'line_note': ['', '']})
check('balanced journal saved', value('SELECT COUNT(*) c FROM journals') == 1)
admin.post('/journals/new', {'jdate': db.today(), 'note': 'broken', 'ref': '',
                             'account_id': [str(cash_account)], 'debit': ['5'],
                             'credit': ['0'], 'line_note': ['']})
check('unbalanced journal rejected', value('SELECT COUNT(*) c FROM journals') == 1)
status, headers, body = admin.get('/trial-balance')
check('trial balance totals match', '1,000.00' in body, status)
for page in ('/balance-sheet', '/cash-flow', '/equity', '/journals', '/accounts'):
    status, headers, body = admin.get(page)
    check(f'accounting page {page}', status.startswith('200'), status)

# --------------------------------------------------------------------------- #
section('reports and Excel exports')
# --------------------------------------------------------------------------- #
for page in ('/reports/profit-item', '/reports/profit-invoice', '/reports/pl', '/reports/stock',
             '/reports/expiry', '/reports/reorder', '/reports/sellers', '/reports/statement',
             '/reports/aging', '/reports/cashflow', '/reports/treasury', '/reports/tax',
             '/reports/item-card', '/reports/purchases', '/reports/sales-customers',
             '/reports/transfers'):
    status, headers, body = admin.get(page)
    check(f'report {page}', status.startswith('200'), status)

status, headers, body = admin.get('/reports/pl')
check('P&L shows a net profit line', 'صافي الربح' in body)
status, headers, body = admin.get('/reports/reorder')
check('reorder report lists the low stock item', 'Low stock item' in body)

status, headers, body = admin.get('/items/export')
check('items export is a real xlsx', status.startswith('200') and
      'spreadsheetml' in headers.get('Content-Type', ''), headers.get('Content-Type'))
parsed = xlsx.read_xlsx(admin.request('/items/export')[2])
check('exported workbook parses with headers',
      bool(parsed) and str(parsed[0][0]).strip() in ('الكود', 'code', 'Code'),
      parsed[0][:3] if parsed else None)

status, headers, body = admin.get('/reports/stock?export=1')
check('report export returns xlsx', status.startswith('200') and
      'spreadsheetml' in headers.get('Content-Type', ''))

import_rows = [['code', 'name', 'category', 'purchase', 'sale', 'reorder', 'barcode', 'stock'],
               ['X1', 'Imported item one', 'Imported', 10, 15, 5, '9990001', 7],
               ['X2', 'Imported item two', 'Imported', 20, 30, 5, '9990002', 0]]
import_bytes = xlsx.write_xlsx(import_rows)
status, headers, body = admin.post('/items/import', {'update_existing': 'on'},
                                   files={'file': ('import.xlsx', import_bytes,
                                                   'application/vnd.openxmlformats-officedocument.'
                                                   'spreadsheetml.sheet')})
check('Excel import created items', value("SELECT COUNT(*) c FROM items WHERE code='X1'") == 1)
check('imported opening stock applied',
      value('SELECT SUM(qty) v FROM stock_movements m JOIN items i ON i.id=m.item_id '
            "WHERE i.code='X1'") == 7)
status, headers, body = admin.get('/items/template')
check('import template downloads', status.startswith('200') and 'spreadsheetml' in
      headers.get('Content-Type', ''))

# --------------------------------------------------------------------------- #
section('barcodes and labels')
# --------------------------------------------------------------------------- #
codes = barcode_lib.encode('1234567890')
check('code128 encode produces start/data/checksum/stop',
      codes and codes[0] == 105 and codes[-1] == 106 and len(codes) == 8, codes)
check('code128 pattern table is valid',
      len(barcode_lib.PATTERNS) == 107 and
      all(sum(pattern) in (11, 13) for pattern in barcode_lib.PATTERNS))
svg = barcode_lib.svg('ABC-123')
check('barcode svg contains bars and label',
      svg.startswith('<svg') and '<rect' in svg and 'ABC-123' in svg)
status, headers, body = admin.post('/barcode', {'ids': [str(item)], 'count': '2',
                                                'size': 'small'})
check('label sheet renders svg barcodes',
      status.startswith('200') and '<svg' in body and 'label' in body, status)

# --------------------------------------------------------------------------- #
section('search, audit, demo data')
# --------------------------------------------------------------------------- #
payload = admin.json('/api/search?q=Acme')
check('search API returns parties',
      payload.get('ok') and any('Acme' in entry['label'] for entry in payload.get('parties', [])),
      payload)
status, headers, body = admin.get('/search?q=Acme')
check('search page lists results', status.startswith('200') and 'Acme' in body)
status, headers, body = admin.get('/audit')
check('audit log recorded activity',
      status.startswith('200') and ('إضافة' in body or 'create' in body), status)

before_demo = value('SELECT COUNT(*) c FROM items')
admin.post('/demo/load')
check('demo data loads', value('SELECT COUNT(*) c FROM items') > before_demo)
admin.post('/demo/clear')
check('demo data clears', value("SELECT COUNT(*) c FROM items WHERE code LIKE 'DM-%'") == 0)

# --------------------------------------------------------------------------- #
section('licence generator (admin)')
# --------------------------------------------------------------------------- #
import license as license_lib  # noqa: E402

status, headers, body = admin.get('/license')
check('licence screen renders for admin',
      status.startswith('200') and 'SNDX' in body and 'مولّد المفاتيح' in body, status)
check('administrator never sees a trial banner',
      'نسخة تجريبية' not in admin.get('/')[2] and
      'فترة تجريبية' not in admin.get('/')[2])

key = license_lib.generate('Acme Holding', 'pro', 5, 365, machine=license_lib.machine_id())
result = license_lib.verify(key)
check('generated key verifies', result['ok'] and result['payload']['s'] == 5, result)
tampered = key[:-4] + ('AAAA' if key[-4:] != 'AAAA' else 'BBBB')
check('tampered key is rejected', license_lib.verify(tampered)['reason'] == 'signature')
expired = license_lib.generate('Acme Holding', 'standard', 1, expires='2000-01-01')
check('expired key is rejected', license_lib.verify(expired)['reason'] == 'expired')
foreign = license_lib.generate('Other PC', 'standard', 1, 30, machine='ZZZZZZZZZZZZ')
check('machine-bound key is rejected elsewhere',
      license_lib.verify(foreign)['reason'] == 'machine' and
      license_lib.verify(foreign, check_machine=False)['ok'])

status, headers, body = admin.post('/license/generate', {
    'customer': 'Licensed Customer', 'edition': 'pro', 'seats': '3', 'days': '365',
    'expires': '', 'bind': 'machine'})
page = admin.get('/license')[2]
found = re.search(r'SNDX-[A-Z0-9.\-]{20,}', page)
check('generator produces a key in the UI', status.startswith('302') and bool(found))
generated_key = found.group(0) if found else ''
status, headers, body = admin.post('/license/activate', {'key': generated_key})
check('key activates', status.startswith('302') and
      value("SELECT COUNT(*) c FROM licenses WHERE status='active'") == 1)
check('licence state becomes licensed', license_lib.state(db.connect())['state'] == 'licensed')
check('trial banner disappears after activation', 'نسخة تجريبية' not in admin.get('/')[2])
status, headers, body = admin.get('/license/download')
check('licence file downloads', status.startswith('200') and 'Sanadex.wanis.online' in body)
active_id = value("SELECT id FROM licenses WHERE status='active'")
admin.post('/license/revoke', {'id': str(active_id)})
check('revoking returns to trial',
      value("SELECT COUNT(*) c FROM licenses WHERE status='active'") == 0 and
      license_lib.state(db.connect())['state'] == 'trial')

# --------------------------------------------------------------------------- #
section('permissions and per-user treasury / store scope')
# --------------------------------------------------------------------------- #
admin.post('/roles/new', {'name': 'Sales only', 'notes': '', 'p_sell': 'on'})
sales_role = value("SELECT id FROM roles WHERE name='Sales only'")
admin.post('/users/new', {'username': 'seller', 'password': 'seller123',
                          'full_name': 'Seller One', 'phone': '', 'email': '',
                          'role_id': str(sales_role), 'treasury_id': str(cash),
                          'store_id': str(store_main), 'lang': 'ar', 'is_admin': '',
                          'active': 'on', 'notes': ''})
check('user created with role', value("SELECT COUNT(*) c FROM users WHERE username='seller'") == 1)

seller = Client()
status, headers, body = seller.post('/login', {'username': 'seller', 'password': 'seller123'})
check('limited user can sign in', status.startswith('302'), status)
status, headers, body = seller.get('/customers')
check('limited user reaches sales screens', status.startswith('200'), status)
status, headers, body = seller.get('/items')
check('inventory blocked for sales-only role', status.startswith('403'), status)
status, headers, body = seller.get('/users')
check('user admin blocked for non-admin', status.startswith('403'), status)
status, headers, body = seller.get('/settings')
check('settings blocked for non-admin', status.startswith('403'), status)
status, headers, body = seller.get('/reports/pl')
check('reports blocked without permission', status.startswith('403'), status)
status, headers, body = seller.get('/license')
check('licence generator blocked for non-admin', status.startswith('403'), status)
status, headers, body = seller.get('/invoice/sale/new')
check('scoped user only sees the assigned treasury',
      'Cash box' in body and 'Bank account' not in body, status)
status, headers, body = seller.get('/')
check('sidebar hides forbidden groups',
      'href="/items"' not in body and 'href="/settings"' not in body and
      'href="/customers"' in body, status)


# --------------------------------------------------------------------------- #
section('per-user licensing: admin lifetime, everyone else 14-day trial')
# --------------------------------------------------------------------------- #
import time as time_module  # noqa: E402
import license as license_module  # noqa: E402

admin_start = admin.get('/')[2]
check('administrator holds a lifetime licence',
      license_module.user_state(conn(), row("SELECT * FROM users WHERE username='admin'"))['state'] == 'lifetime')
check('licence screen states the admin licence is permanent',
      'ترخيصك دائم' in admin.get('/license')[2])

seller_row = row("SELECT * FROM users WHERE username='seller'")
check('trial clock starts on the user first login', (seller_row['trial_started'] or 0) > 0)
seller_state = license_module.user_state(conn(), seller_row)
check('non-admin starts a 14 day trial',
      seller_state['state'] == 'trial' and seller_state['days_left'] == 14, seller_state)
check('user dashboard shows a personal trial strip',
      'فترة تجريبية لحسابك' in seller.get('/')[2])
check('licence screen stays admin-only for users', seller.get('/license')[0].startswith('403'))

# expire the personal trial
expire_conn = conn()
expire_conn.execute('UPDATE users SET trial_started=? WHERE id=?',
                    (time_module.time() - 20 * 86400, seller_row['id']))
expire_conn.commit()
expire_conn.close()
expired_state = license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))
check('trial expires after 14 days',
      expired_state['state'] == 'trial_expired' and expired_state['days_left'] == 0, expired_state)
status, headers, body = seller.get('/customers')
check('expired trial blocks work screens',
      status.startswith('403') and 'انتهت الفترة التجريبية' in body, status)
check('expired page keeps the data safe message', 'البيانات محفوظة' in body)
status, headers, body = seller.get('/logout')
check('expired user can still sign out', status.startswith('302'), status)
seller.post('/login', {'username': 'seller', 'password': 'seller123'})
status, headers, body = seller.get('/')
check('expired user is still blocked after re-login', status.startswith('403'), status)

admin.post('/license/user', {'uid': str(seller_row['id']), 'action': 'grant'})
granted_state = license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))
check('admin can grant a user a lifetime licence', granted_state['state'] == 'licensed', granted_state)
status, headers, body = seller.get('/customers')
check('granted user works normally again',
      status.startswith('200') and 'فترة تجريبية' not in body, status)

admin.post('/license/user', {'uid': str(seller_row['id']), 'action': 'revoke'})
check('revoking returns the user to a fresh trial',
      license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))['state'] == 'trial')
admin.post('/license/user', {'uid': str(seller_row['id']), 'action': 'reset'})
reset_conn = conn()
reset_conn.execute('UPDATE users SET trial_started=? WHERE id=?',
                   (time_module.time() - 5 * 86400, seller_row['id']))
reset_conn.commit()
reset_conn.close()
admin.post('/license/user', {'uid': str(seller_row['id']), 'action': 'reset'})
reset_state = license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))
check('admin can reset a trial back to 14 days',
      reset_state['state'] == 'trial' and reset_state['days_left'] == 14, reset_state)

status, headers, body = admin.get('/license')
check('licence screen lists user trials', 'المستخدمون والاشتراكات' in body and 'seller' in body)
check('admin can renew a trial with the subscription plans',
      'value="renew"' in body and 'value="3m"' in body and 'value="1y"' in body)
check('licence page has a quick trial-user + plan dropdown',
      'id="quick_uid"' in body and 'id="quick_plan"' in body)

installation_key = license_module.generate('Installation licence', 'standard', 5, 365)
admin.post('/license/activate', {'key': installation_key})
covered = license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))
check('a valid installation licence covers every user', covered['state'] == 'licensed', covered)
status, headers, body = seller.get('/customers')
check('covered user sees no trial strip',
      status.startswith('200') and 'فترة تجريبية' not in body, status)


# --------------------------------------------------------------------------- #
section('public language switch and self sign-up')
# --------------------------------------------------------------------------- #
visitor = Client()
status, headers, body = visitor.get('/login')
check('login page starts in Arabic', 'مرحباً بعودتك' in body and 'dir="rtl"' in body)
status, headers, body = visitor.get('/lang/en?next=/login')
check('guest can switch the language before signing in', status.startswith('302'), status)
status, headers, body = visitor.get('/login')
check('login page now renders in English',
      'Welcome back' in body and 'dir="ltr"' in body, body[:120])
status, headers, body = visitor.get('/lang/ar?next=/login')
status, headers, body = visitor.get('/login')
check('guest can switch back to Arabic', 'مرحباً بعودتك' in body and 'dir="rtl"' in body)

enable = conn()
enable.execute('UPDATE company SET allow_signup=1 WHERE id=1')
enable.execute("UPDATE licenses SET status='revoked' WHERE status='active'")
enable.commit()
enable.close()

signup_client = Client()
status, headers, body = signup_client.get('/signup')
check('sign-up page is public',
      status.startswith('200') and 'إنشاء حساب جديد' in body, status)
check('sign-up shows the subscription plans',
      'شهر واحد' in body and '3 أشهر' in body and 'سنة كاملة' in body and 'مدى الحياة' in body)
check('one-year plan is 2,999', '2,999.00' in body and 'سنة كاملة' in body)
check('login page links to sign-up', 'أنشئ حساباً' in visitor.get('/login')[2])

new_account = {'full_name': 'New Cashier', 'username': 'cashier1', 'phone': '0100',
               'email': 'cashier@example.com', 'password': 'Pass1234', 'password2': 'Pass1234'}
status, headers, body = signup_client.post('/signup', new_account)
new_user = row("SELECT * FROM users WHERE username='cashier1'")
check('sign-up activates the account immediately',
      status.startswith('302') and new_user and new_user['active'] == 1,
      new_user and new_user['active'])
check('sign-up starts the 14-day trial at creation',
      new_user and (new_user['trial_started'] or 0) > 0)
check('new account is not an administrator', new_user['is_admin'] == 0)
check('new account gets the built-in Staff role',
      new_user['role_id'] == value("SELECT id FROM roles WHERE name='Staff' and is_system=1"))
status, headers, body = admin.get('/users')
check('users list shows the active account',
      'cashier1' in body and 'بانتظار الموافقة' not in body)

new_client = Client()
status, headers, body = new_client.post('/login',
                                       {'username': 'cashier1', 'password': 'Pass1234'})
check('new account can sign in immediately', status.startswith('302'), status)
new_state = license_module.user_state(conn(), row("SELECT * FROM users WHERE username='cashier1'"))
check('new account has a 14-day trial', new_state['state'] == 'trial' and new_state['days_left'] == 14,
      new_state)
status, headers, body = new_client.get('/items')
check('new account can use the features', status.startswith('200'), status)
status, headers, body = new_client.get('/license')
check('license generation stays admin-only for new users', status.startswith('403'), status)
for admin_path in ('/users', '/roles'):
    status, headers, body = new_client.get(admin_path)
    check(f'new user cannot manage {admin_path}', status.startswith('403'), status)
status, headers, body = new_client.get('/')
check('new user sidebar hides the licence generator',
      'href="/license"' not in body and 'href="/items"' in body, status)
check('no pending-approval warning for the admin', 'بانتظار موافقتك' not in admin.get('/')[2])

status, headers, body = signup_client.post('/signup', dict(new_account, username='cashier1'))
check('duplicate username is rejected',
      status.startswith('200') and 'مستخدم بالفعل' in body, status)
status, headers, body = signup_client.post('/signup', dict(new_account, username='cashier2',
                                                          password='123', password2='123'))
check('short password is rejected', 'قصيرة' in body)
status, headers, body = signup_client.post('/signup', dict(new_account, username='cashier2',
                                                          password2='Different1'))
check('password confirmation is enforced', 'غير متطابقتين' in body)

# a paid plan chosen at sign-up becomes a request the admin can activate
plan_account = dict(new_account, username='cashier2', full_name='Plan Buyer',
                    password='Pass1234', password2='Pass1234', plan='3m')
status, headers, body = signup_client.post('/signup', plan_account)
plan_user = row("SELECT * FROM users WHERE username='cashier2'")
pending_request = row("SELECT * FROM subscriptions WHERE user_id=? AND status='pending'",
                      (plan_user['id'],))
check('a chosen plan is stored as a pending request',
      pending_request and pending_request['plan'] == '3m' and pending_request['price'] == 999,
      pending_request)
licence_page = admin.get('/license')[2]
check('admin licence page lists the subscription request',
      'cashier2' in licence_page and '3 أشهر' in licence_page)
admin.post('/license/user', {'uid': str(plan_user['id']), 'action': 'renew', 'plan': '3m'})
plan_state = license_module.user_state(conn(), row("SELECT * FROM users WHERE id=?", (plan_user['id'],)))
check('admin activates the requested plan',
      plan_state['state'] == 'licensed' and plan_state['plan'] == '3m', plan_state)
check('activation clears the pending request',
      value("SELECT COUNT(*) c FROM subscriptions WHERE user_id=? AND status='pending'",
            (plan_user['id'],)) == 0)
status, headers, body = admin.get(f"/license/share/{plan_user['id']}")
check('admin can share the user licence as text',
      status.startswith('200') and 'cashier2' in body and '3 months' in body, status)
admin.post('/license/user', {'uid': str(plan_user['id']), 'action': 'cancel'})
check('cancelling a subscription returns the user to the trial',
      license_module.user_state(conn(), row("SELECT * FROM users WHERE id=?",
                                            (plan_user['id'],)))['state'] == 'trial')

# Telegram bot builders run against the same data (no network needed)
import telegram_bot  # noqa: E402
bot_cfg = {'token': '', 'chat_ids': [], 'public_url': 'https://example.test',
           'currency': 'EGP', 'enabled': False, 'digest_hour': 21, 'company': 'Sanadex'}
bot_conn = conn()
stats_text, stats_kb = telegram_bot.build_stats(bot_conn, bot_cfg)
check('bot stats report revenue and licences',
      'Revenue:' in stats_text and 'Active licences:' in stats_text and stats_kb is None)
check('bot menu is only built on demand',
      'inline_keyboard' in telegram_bot.menu_keyboard(bot_cfg))
license_module.request_subscription(bot_conn, plan_user['id'], '6m', 'test')
pending_text, pending_kb = telegram_bot.build_pending(bot_conn, bot_cfg)
check('bot lists pending subscriptions with approve buttons',
      'cashier2' in pending_text and 'approve:' in str(pending_kb), pending_text)
sub_id = value("SELECT id FROM subscriptions WHERE user_id=? AND status='pending'", (plan_user['id'],))
approve_text = telegram_bot.perform_approve(bot_conn, sub_id, 'test')
check('bot approves a pending subscription', 'Approved' in approve_text and
      license_module.user_state(bot_conn, row("SELECT * FROM users WHERE id=?", (plan_user['id'],)))['plan'] == '6m')
renew_text = telegram_bot.perform_renew(bot_conn, bot_cfg, plan_user['id'], '1m')
check('bot renews a subscription by 30 days', 'Renewed' in renew_text)
find_text, find_kb = telegram_bot.build_find(bot_conn, bot_cfg, 'cashier2')
check('bot looks up a subscription', 'cashier2' in find_text and 'Plan:' in find_text)
expiry_text, expiry_kb = telegram_bot.build_expiry(bot_conn, bot_cfg)
check('bot reports expiring subscriptions', 'Expiring' in expiry_text or 'No subscriptions' in expiry_text)
health_text, health_kb = telegram_bot.build_health(bot_conn, bot_cfg)
check('bot health check works', 'Database: OK' in health_text and health_kb is None)
sent = []
original_send = telegram_bot.send
telegram_bot.send = lambda cfg, text, chat_id=None, keyboard=None: sent.append(text) or True
try:
    telegram_bot.handle_update({'message': {'chat': {'id': 42}, 'text': '/health'}},
                               dict(bot_cfg, chat_ids=['42']))
    telegram_bot.handle_update({'message': {'chat': {'id': 7}, 'text': '/health'}},
                               dict(bot_cfg, chat_ids=['42']))
finally:
    telegram_bot.send = original_send
check('bot answers an authorised command and ignores strangers',
      len(sent) == 1 and 'Database: OK' in sent[0], sent)
original_api = telegram_bot._api
telegram_bot._api = lambda method, payload, token: {'ok': True, 'result': [
    {'message': {'chat': {'id': 4242, 'first_name': 'Owner', 'username': 'owner'}}}]}
try:
    chats = telegram_bot.recent_chats('token')
    check('bot detects recent chats', chats and chats[0]['id'] == '4242', chats)
    detect_conn = conn()
    detect_conn.execute("UPDATE company SET telegram_token='test-token' WHERE id=1")
    detect_conn.commit()
    detect_conn.close()
    status, headers, body = admin.post('/license/telegram/detect', {'telegram_token': 'test-token'})
    detected = value('SELECT telegram_chat_id FROM company WHERE id=1')
    check('detect button saves the chat id',
          status.startswith('302') and str(detected) == '4242', detected)
finally:
    telegram_bot._api = original_api
os.environ['SANADEX_PUBLIC_URL'] = 'https://env.example'
os.environ['SANADEX_TELEGRAM_DIGEST_HOUR'] = '7'
try:
    env_cfg = telegram_bot.config()
finally:
    os.environ.pop('SANADEX_PUBLIC_URL', None)
    os.environ.pop('SANADEX_TELEGRAM_DIGEST_HOUR', None)
check('Coolify env overrides the bot public URL and digest hour',
      env_cfg['public_url'] == 'https://env.example' and env_cfg['digest_hour'] == 7, env_cfg)
bot_conn.close()


disable = conn()
disable.execute('UPDATE company SET allow_signup=0 WHERE id=1')
disable.commit()
disable.close()
status, headers, body = signup_client.get('/signup')
check('sign-up can be disabled by the admin',
      'معطّل حالياً' in body, status)
before = value("SELECT COUNT(*) c FROM users")
signup_client.post('/signup', dict(new_account, username='cashier3'))
check('disabled sign-up creates nothing', value("SELECT COUNT(*) c FROM users") == before)

# --------------------------------------------------------------------------- #
section('backup, download and restore')
# --------------------------------------------------------------------------- #
status, headers, body = admin.post('/backup/now')
backups = db.backup_files()
check('backup file created', status.startswith('302') and len(backups) >= 1, len(backups))
latest = backups[0]['name']
status, headers, body = admin.get(f'/backup/download/{latest}')
check('backup downloads', status.startswith('200') and
      headers.get('Content-Type') == 'application/octet-stream', status)
customer_count = value('SELECT COUNT(*) c FROM customers')
admin.post(f'/customers/{customer}/delete')
check('customer deleted before restore',
      value('SELECT COUNT(*) c FROM customers') == customer_count - 1)
status, headers, body = admin.post('/backup/restore', {'existing': latest})
check('restore accepted and signs out', status.startswith('302'), status)
admin.post('/login', {'username': 'admin', 'password': 'admin123'})
check('data restored from backup',
      value("SELECT COUNT(*) c FROM customers WHERE name='Acme Retail'") == 1)


# --------------------------------------------------------------------------- #
section('maintenance CLI, health endpoint and deployment assets')
# --------------------------------------------------------------------------- #
import contextlib  # noqa: E402
import manage  # noqa: E402
import framework as framework_module  # noqa: E402

reset_path = os.path.join(BASE, 'test-reset.db')
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(reset_path + suffix)
    except OSError:
        pass

with contextlib.redirect_stdout(io.StringIO()):
    manage.main(['--db', reset_path, 'init'])
    manage.main(['--db', reset_path, 'reset-admin', '--username', 'admin',
                 '--password', 'TempPass1'])
import sqlite3 as sqlite_module  # noqa: E402

reset_conn = sqlite_module.connect(reset_path)
reset_conn.row_factory = sqlite_module.Row
reset_conn.execute('UPDATE users SET active=0 WHERE username=?', ('admin',))
reset_conn.commit()
reset_conn.close()
with contextlib.redirect_stdout(io.StringIO()):
    manage.main(['--db', reset_path, 'reset-admin', '--username', 'admin',
                 '--password', 'TempPass2'])
reset_conn = sqlite_module.connect(reset_path)
reset_conn.row_factory = sqlite_module.Row
admin_row = reset_conn.execute("SELECT * FROM users WHERE username='admin'").fetchone()
reset_conn.close()
check('manage.py resets the admin password and re-enables the account',
      framework_module.check_password('TempPass2', admin_row['password']) and
      admin_row['active'] == 1)

guest_health = Client()
health_status, health_headers, health_body = guest_health.get('/healthz')
health_payload = {}
try:
    health_payload = json.loads(health_body)
except ValueError:
    pass
check('health endpoint is public and reports readiness',
      health_status.startswith('200') and health_payload.get('ok') is True and
      health_payload.get('app') == 'Sanadex.wanis.online', health_body[:200])

required_assets = ('manage.py', 'run_prod.py', 'run-prod.bat',
                   'Dockerfile', 'docker-compose.yml', '.env.example',
                   'DEPLOY.md', 'DEPLOY_AR.md', 'deploy/nginx.conf', 'deploy/Caddyfile',
                   'deploy/sanadex.service', '.dockerignore')
missing_assets = [name for name in required_assets
                  if not os.path.isfile(os.path.join(BASE, name))]
check('deployment and recovery assets are shipped', not missing_assets, missing_assets)

for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(reset_path + suffix)
    except OSError:
        pass


# missing database + existing backups must never silently reset the admin
guard_dir = os.path.join(BASE, 'test-guard')
_shutil.rmtree(guard_dir, ignore_errors=True)
os.makedirs(os.path.join(guard_dir, 'backups'), exist_ok=True)
with open(os.path.join(guard_dir, 'backups', 'sanadex-backup-guard.db'), 'wb') as handle:
    handle.write(b'not-a-real-database')
saved_db, saved_backups = db.DB_PATH, db.BACKUP_DIR
saved_fresh = os.environ.pop('SANADEX_FRESH', None)
db.DB_PATH = os.path.join(guard_dir, 'missing.db')
db.BACKUP_DIR = os.path.join(guard_dir, 'backups')
guard_raised = False
try:
    db.init_db()
except RuntimeError as exc:
    guard_raised = 'Refusing to start' in str(exc)
finally:
    db.DB_PATH, db.BACKUP_DIR = saved_db, saved_backups
    if saved_fresh is not None:
        os.environ['SANADEX_FRESH'] = saved_fresh
    _shutil.rmtree(guard_dir, ignore_errors=True)
check('a missing database with backups refuses to start empty', guard_raised)

# --------------------------------------------------------------------------- #
section('result')
# --------------------------------------------------------------------------- #
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(TEST_DB + suffix)
    except OSError:
        pass
_shutil.rmtree(TEST_BACKUPS, ignore_errors=True)
_shutil.rmtree(TEST_UPLOADS, ignore_errors=True)

total = len(PASSED) + len(FAILED)
print(f'\n===== {len(PASSED)} passed, {len(FAILED)} failed, {total} checks =====')
if FAILED:
    for name, extra in FAILED:
        print(f'  FAILED: {name} :: {str(extra)[:300]}')
    sys.exit(1)
