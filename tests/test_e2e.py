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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

TEST_DB = os.path.join(BASE, 'test-sanadex.db')
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(TEST_DB + suffix)
    except OSError:
        pass

import db  # noqa: E402
db.DB_PATH = TEST_DB
db.BACKUP_DIR = os.path.join(BASE, 'backups')
db.init_db()

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

    def request(self, path, method='GET', data=None, body=None, content_type=None):
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
check('licence screen lists user trials', 'تجارب المستخدمين' in body and 'seller' in body)

installation_key = license_module.generate('Installation licence', 'standard', 5, 365)
admin.post('/license/activate', {'key': installation_key})
covered = license_module.user_state(conn(), row('SELECT * FROM users WHERE id=?', (seller_row['id'],)))
check('a valid installation licence covers every user', covered['state'] == 'licensed', covered)
status, headers, body = seller.get('/customers')
check('covered user sees no trial strip',
      status.startswith('200') and 'فترة تجريبية' not in body, status)

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

required_assets = ('manage.py', 'run_prod.py', 'run-prod.bat', 'reset-admin.bat',
                   'reset-admin.sh', 'Dockerfile', 'docker-compose.yml', '.env.example',
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

# --------------------------------------------------------------------------- #
section('result')
# --------------------------------------------------------------------------- #
for suffix in ('', '-wal', '-shm'):
    try:
        os.remove(TEST_DB + suffix)
    except OSError:
        pass

total = len(PASSED) + len(FAILED)
print(f'\n===== {len(PASSED)} passed, {len(FAILED)} failed, {total} checks =====')
if FAILED:
    for name, extra in FAILED:
        print(f'  FAILED: {name} :: {str(extra)[:300]}')
    sys.exit(1)
