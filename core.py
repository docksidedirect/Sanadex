# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - shared application core.

Rendering, bilingual context, navigation, permission guards, stock/cash
primitives, audit trail, pagination and Excel exports live here so every
feature module stays thin and consistent.
"""
import datetime
import os
import time

import barcode as barcode_lib
import db
import i18n
import license as license_lib
import xlsx
from db import connect, ex, one, q, scalar, today, x
from framework import (APP_NAME, Request, Response, file_response, forbidden,
                       hash_password, html, json_response, not_found, read_upload,
                       redirect, router, url_for)
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE = os.path.dirname(os.path.abspath(__file__))
TPL = Environment(loader=FileSystemLoader(os.path.join(BASE, 'templates')),
                  autoescape=select_autoescape(['html']), trim_blocks=True,
                  lstrip_blocks=True)
VERSION = '3.0'
TPL.globals['url_for'] = url_for
TPL.globals['brand'] = APP_NAME
TPL.globals['barcode_svg'] = barcode_lib.svg


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def money(value):
    try:
        return f'{float(value):,.2f}'
    except (TypeError, ValueError):
        return ''


def safe_float(value, default=0.0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ''
    return str(int(f)) if f.is_integer() else f'{f:,.2f}'.rstrip('0').rstrip('.')


def pct(value):
    try:
        return f'{float(value):g}%'
    except (TypeError, ValueError):
        return ''


TPL.filters['money'] = money
TPL.filters['num'] = num
TPL.filters['pct'] = pct
TPL.filters['amount'] = money


def stamp(value, fmt='%Y-%m-%d %H:%M'):
    try:
        return time.strftime(fmt, time.localtime(float(value)))
    except (TypeError, ValueError):
        return '—'


TPL.filters['stamp'] = stamp


# --------------------------------------------------------------------------- #
# icons (inline sprite lives in base.html)
# --------------------------------------------------------------------------- #
def icon(name, cls='ic'):
    return f'<svg class="{cls}" aria-hidden="true" focusable="false"><use href="#i-{name}"></use></svg>'


TPL.globals['icon'] = icon


# --------------------------------------------------------------------------- #
# language / theme
# --------------------------------------------------------------------------- #
def current_lang(req):
    lang = (req.session.get('lang') or (req.user or {}).get('lang')
            or (req.company or {}).get('lang') or 'ar')
    return i18n.lang_of(lang)


def current_theme(req):
    return req.session.get('theme') or (req.company or {}).get('theme') or 'light'


def T(req, key, **kw):
    return i18n.t(key, current_lang(req), **kw)


# --------------------------------------------------------------------------- #
# navigation
# --------------------------------------------------------------------------- #
NAV = [
    ('overview', 'nav.overview', 'grid', [
        ('dashboard', 'dashboard', 'grid', None),
        ('reminders', 'reminders', 'bell', None),
    ]),
    ('sales', 'nav.sales', 'cart', [
        ('sale_invoices', 'sales_invoices', 'file-text', 'p_sell'),
        ('sale_orders', 'sale_orders', 'clipboard', 'p_sell'),
        ('customers', 'customers', 'users', 'p_sell'),
        ('credit_notes', 'credit_note_customer', 'receipt', 'p_sell'),
        ('price_tiers', 'price_tiers', 'tag', 'p_sell'),
        ('offers', 'offers', 'percent', 'p_sell'),
    ]),
    ('purchases', 'nav.purchases', 'truck', [
        ('purchase_invoices', 'purchase_invoices', 'file-text', 'p_buy'),
        ('purchase_orders', 'purchase_orders', 'clipboard', 'p_buy'),
        ('suppliers', 'suppliers', 'briefcase', 'p_buy'),
        ('sup_credit_notes', 'credit_note_supplier', 'receipt', 'p_buy'),
    ]),
    ('inventory', 'nav.inventory', 'box', [
        ('items', 'items', 'box', 'p_inv'),
        ('stores', 'stores', 'store', 'p_inv'),
        ('transfers', 'store_transfers', 'swap', 'p_inv'),
        ('damages', 'damages', 'alert', 'p_inv'),
        ('barcode', 'barcode_labels', 'barcode', 'p_inv'),
        ('item_import', 'import_items', 'upload', 'p_inv'),
    ]),
    ('money', 'nav.money', 'wallet', [
        ('treasuries', 'treasuries', 'wallet', 'p_treasury'),
        ('treasury_transfers', 'treasury_transfers', 'swap', 'p_treasury'),
        ('vouchers', 'vouchers', 'receipt', 'p_treasury'),
        ('withdrawals', 'withdrawals', 'arrow-down', 'p_treasury'),
        ('cheques', 'cheques', 'credit-card', 'p_treasury'),
    ]),
    ('reports', 'nav.reports', 'chart', [
        ('report_profit_item', 'report_profit_item', 'chart', 'p_reports'),
        ('report_profit_invoice', 'report_profit_invoice', 'chart', 'p_reports'),
        ('report_pl', 'report_pl', 'chart', 'p_reports'),
        ('report_stock', 'report_stock', 'box', 'p_reports'),
        ('report_expiry', 'report_expiry', 'clock', 'p_reports'),
        ('report_reorder', 'report_reorder', 'alert', 'p_reports'),
        ('report_sellers', 'report_sellers', 'chart', 'p_reports'),
        ('report_statement', 'report_statement', 'file-text', 'p_reports'),
        ('report_aging', 'report_aging', 'clock', 'p_reports'),
        ('report_cashflow', 'report_cashflow', 'wallet', 'p_reports'),
        ('report_treasury', 'report_treasury', 'wallet', 'p_reports'),
        ('report_tax', 'report_tax', 'percent', 'p_reports'),
        ('report_item_card', 'report_item_card', 'layers', 'p_reports'),
        ('report_purchases', 'report_purchases', 'truck', 'p_reports'),
        ('report_sales_customer', 'report_sales_customer', 'users', 'p_reports'),
        ('report_transfers', 'report_transfers', 'swap', 'p_reports'),
    ]),
    ('accounting', 'nav.accounting', 'book', [
        ('accounts', 'chart_of_accounts', 'layers', 'p_acc'),
        ('journals', 'journal_entries', 'book', 'p_acc'),
        ('trial_balance', 'trial_balance', 'chart', 'p_acc'),
        ('balance_sheet', 'balance_sheet', 'file-text', 'p_acc'),
        ('cash_flow', 'cash_flow', 'wallet', 'p_acc'),
        ('equity_statement', 'equity_statement', 'layers', 'p_acc'),
    ]),
    ('installments', 'nav.installments', 'calendar', [
        ('installments', 'installment_plans', 'calendar', 'p_inst'),
        ('installment_due', 'due_installments', 'clock', 'p_inst'),
    ]),
    ('services', 'nav.services', 'wrench', [
        ('services', 'service_orders', 'wrench', 'p_services'),
    ]),
    ('hr', 'nav.hr', 'id', [
        ('employees', 'employees', 'id', 'p_hr'),
        ('job_roles', 'job_roles', 'briefcase', 'p_hr'),
        ('salary_packages', 'salary_packages', 'wallet', 'p_hr'),
        ('attendance', 'attendance', 'clock', 'p_hr'),
        ('holidays', 'holidays', 'calendar', 'p_hr'),
        ('bonusded', 'bonus_deduction', 'plus', 'p_hr'),
        ('advances', 'advances', 'arrow-down', 'p_hr'),
        ('payroll', 'payroll', 'receipt', 'p_hr'),
    ]),
    ('admin', 'nav.admin', 'settings', [
        ('users', 'users', 'users', 'admin'),
        ('roles', 'roles', 'shield', 'admin'),
        ('license', 'license', 'key', 'admin'),
        ('settings', 'settings', 'settings', 'p_settings'),
        ('backup_restore', 'backup', 'database', 'admin'),
        ('audit', 'audit_log', 'list', 'admin'),
        ('system_info', 'system_info', 'info', 'admin'),
    ]),
]


def permissions(req):
    user = req.user or {}
    return (user.get('perms') or {}) if user else {}


def can(req, perm=None):
    user = req.user or {}
    if not user:
        return False
    if user.get('is_admin'):
        return True
    if not perm:
        return True
    if perm == 'admin':
        return False
    return bool(permissions(req).get(perm))


def build_nav(req):
    groups = []
    for key, label_key, icon_name, items in NAV:
        allowed = []
        for route_name, label, ic, perm in items:
            if perm == 'admin':
                if not (req.user or {}).get('is_admin'):
                    continue
            elif not can(req, perm):
                continue
            allowed.append({'route': route_name, 'label': label, 'icon': ic,
                            'href': url_for(route_name)})
        if allowed:
            groups.append({'key': key, 'label': label_key, 'icon': icon_name, 'items': allowed})
    return groups


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def flash(req, message, category='info'):
    req.session.setdefault('flash', []).append({'msg': message, 'cat': category})


def render(req, template, http_status='200 OK', **ctx):
    user = req.user or {}
    company = req.company or (one(req.db, 'SELECT * FROM company WHERE id=1') or {})
    lang = current_lang(req)
    flashes = req.session.pop('flash', [])
    ctx.setdefault('title', APP_NAME)
    base = {
        'user': user,
        'company': company,
        'flashes': flashes,
        'lang': lang,
        'dir': 'rtl' if lang == 'ar' else 'ltr',
        't': lambda key, **kw: i18n.t(key, lang, **kw),
        'nav': build_nav(req),
        'request': req,
        'req': req,
        'now': today(),
        'currency': company.get('currency') or '',
        'theme': current_theme(req),
        'can': lambda perm=None: can(req, perm),
        'i18n': i18n,
        'lic': license_lib.user_state(req.db, user, company),
        'active': ctx.get('active', ''),
        'query': {k: v[0] for k, v in req.query.items()},
    }
    base.update(ctx)
    body = TPL.get_template(template).render(**base)
    response = html(body, http_status)
    response.headers.append(('Cache-Control', 'no-store, must-revalidate'))
    response.set_cookie_header(req)
    return response


def forbidden_page(req, message_key='forbidden'):
    if req.method == 'POST':
        return Response(T(req, message_key), '403 Forbidden')
    return render(req, 'error.html', http_status='403 Forbidden',
                  title=T(req, 'forbidden'), message=T(req, message_key), code='403')


def guard(req, perm=None):
    """Return None when allowed, otherwise a ready response."""
    if not req.user:
        return redirect(url_for('login'), req)
    if perm and not can(req, perm):
        return forbidden_page(req)
    return None


def json_ok(payload=None):
    data = {'ok': True}
    data.update(payload or {})
    return json_response(data)


def json_error(message, code='400 Bad Request'):
    return json_response({'ok': False, 'error': message}, code)


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #
def audit(req, action, entity, entity_id=0, detail=''):
    try:
        user = req.user or {}
        ex(req.db, 'INSERT INTO audit_log(ts,user_id,username,action,entity,entity_id,detail,ip) '
                   'VALUES(?,?,?,?,?,?,?,?)',
           (time.time(), user.get('id') or 0, user.get('username') or '',
            action, entity, int(entity_id or 0), (detail or '')[:240],
            (req.environ.get('REMOTE_ADDR') or '')[:60]))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# documents numbering
# --------------------------------------------------------------------------- #
def next_number(req, kind, commit=True):
    """Return the next formatted document number for sale/purchase/order/voucher."""
    comp = one(req.db, 'SELECT * FROM company WHERE id=1') or {}
    if kind == 'sale':
        prefix, suffix, counter = comp.get('inv_prefix') or '', comp.get('inv_suffix') or '', comp.get('next_sale_no') or 1
        column = 'next_sale_no'
    elif kind == 'purchase':
        prefix, suffix, counter = comp.get('pinv_prefix') or '', comp.get('pinv_suffix') or '', comp.get('next_purchase_no') or 1
        column = 'next_purchase_no'
    elif kind == 'order':
        prefix, suffix, counter = comp.get('order_prefix') or '', '', comp.get('next_order_no') or 1
        column = 'next_order_no'
    else:
        prefix, suffix, counter = comp.get('vch_prefix') or '', '', comp.get('next_voucher_no') or 1
        column = 'next_voucher_no'
    ex(req.db, f'UPDATE company SET {column}={column}+1 WHERE id=1', ())
    return f'{prefix}{counter}{suffix}'


# --------------------------------------------------------------------------- #
# stock & money primitives (all quantities in base units)
# --------------------------------------------------------------------------- #
def get_unit(conn, unit_id):
    return one(conn, 'SELECT * FROM item_units WHERE id=?', (unit_id,)) or {}


def unit_factor(conn, unit_id):
    unit = get_unit(conn, unit_id)
    try:
        return float(unit.get('factor') or 1) or 1.0
    except (TypeError, ValueError):
        return 1.0


def base_qty(conn, unit_id, qty):
    try:
        return float(qty or 0) * unit_factor(conn, unit_id)
    except (TypeError, ValueError):
        return 0.0


def stock_of(conn, item_id, store_id=None):
    if store_id:
        return scalar(conn, 'SELECT COALESCE(SUM(qty),0) v FROM stock_movements '
                            'WHERE item_id=? AND store_id=?', (item_id, store_id))
    return scalar(conn, 'SELECT COALESCE(SUM(qty),0) v FROM stock_movements WHERE item_id=?',
                  (item_id,))


def movement(conn, item_id, unit_id, store_id, qty, ref_type, ref_id, note='', mdate=None, user_id=0):
    conn.execute('INSERT INTO stock_movements(item_id,unit_id,store_id,qty,ref_type,ref_id,'
                 'mdate,note,user_id) VALUES(?,?,?,?,?,?,?,?,?)',
                 (item_id, unit_id, store_id, qty, ref_type, ref_id,
                  mdate or today(), note or '', user_id or 0))
    conn.commit()


def add_batch(conn, item_id, store_id, qty, expiry_date='', batch_no=''):
    if not expiry_date:
        return
    if float(qty or 0) <= 0:
        return
    row = one(conn, 'SELECT id FROM item_batches WHERE item_id=? AND store_id=? AND expiry_date=?',
              (item_id, store_id, expiry_date))
    if row:
        ex(conn, 'UPDATE item_batches SET qty=qty+? WHERE id=?', (qty, row['id']))
    else:
        ex(conn, 'INSERT INTO item_batches(item_id,store_id,qty,expiry_date,batch_no) '
                 'VALUES(?,?,?,?,?)', (item_id, store_id, qty, expiry_date, batch_no))


def consume_batches(conn, item_id, store_id, qty):
    """FIFO by expiry date; leftover goes to a no-expiry bucket so totals stay true."""
    remaining = float(qty or 0)
    if remaining <= 0:
        return
    batches = q(conn, 'SELECT * FROM item_batches WHERE item_id=? AND store_id=? AND qty>0 '
                      'ORDER BY expiry_date', (item_id, store_id))
    for batch in batches:
        if remaining <= 0:
            break
        take = min(float(batch['qty']), remaining)
        ex(conn, 'UPDATE item_batches SET qty=qty-? WHERE id=?', (take, batch['id']))
        remaining -= take
    if remaining > 0:
        row = one(conn, 'SELECT id FROM item_batches WHERE item_id=? AND store_id=? AND expiry_date=?',
                  (item_id, store_id, ''))
        if row:
            ex(conn, 'UPDATE item_batches SET qty=qty-? WHERE id=?', (remaining, row['id']))
        else:
            ex(conn, 'INSERT INTO item_batches(item_id,store_id,qty,expiry_date) VALUES(?,?,?,?)',
               (item_id, store_id, -remaining, ''))


def restore_batch(conn, item_id, store_id, qty, expiry_date=''):
    if float(qty or 0) <= 0:
        return
    row = one(conn, 'SELECT id FROM item_batches WHERE item_id=? AND store_id=? AND expiry_date=?',
              (item_id, store_id, expiry_date or ''))
    if row:
        ex(conn, 'UPDATE item_batches SET qty=qty+? WHERE id=?', (qty, row['id']))
    else:
        ex(conn, 'INSERT INTO item_batches(item_id,store_id,qty,expiry_date) VALUES(?,?,?,?)',
           (item_id, store_id, qty, expiry_date or ''))


def treasury_delta(conn, treasury_id, delta):
    if not treasury_id:
        return
    ex(conn, 'UPDATE treasuries SET balance=balance+? WHERE id=?', (delta, treasury_id))


def party_delta(conn, party_type, party_id, delta):
    if not party_id:
        return
    table = 'customers' if party_type == 'customer' else 'suppliers'
    ex(conn, f'UPDATE {table} SET balance=balance+? WHERE id=?', (delta, party_id))


def party_balance(conn, party_type, party_id):
    if not party_id:
        return 0
    table = 'customers' if party_type == 'customer' else 'suppliers'
    return scalar(conn, f'SELECT balance FROM {table} WHERE id=?', (party_id,))


def party_name(conn, party_type, party_id):
    if not party_id:
        return ''
    table = 'customers' if party_type == 'customer' else 'suppliers'
    row = one(conn, f'SELECT name FROM {table} WHERE id=?', (party_id,))
    return row['name'] if row else ''


def store_name(conn, store_id):
    row = one(conn, 'SELECT name FROM stores WHERE id=?', (store_id,))
    return row['name'] if row else ''


def treasury_name(conn, treasury_id):
    row = one(conn, 'SELECT name FROM treasuries WHERE id=?', (treasury_id,))
    return row['name'] if row else ''


def item_name(conn, item_id):
    row = one(conn, 'SELECT name FROM items WHERE id=?', (item_id,))
    return row['name'] if row else ''


def unit_name(conn, unit_id):
    row = one(conn, 'SELECT name FROM item_units WHERE id=?', (unit_id,))
    return row['name'] if row else ''


def scoped_stores(req):
    """Stores visible to the current user (restricted users see their own)."""
    rows = q(req.db, 'SELECT id,name FROM stores WHERE active=1 ORDER BY name')
    comp = req.company or {}
    user = req.user or {}
    if comp.get('restrict_scope') and user.get('store_id') and not user.get('is_admin'):
        return [r for r in rows if r['id'] == user['store_id']] or rows[:1]
    return rows


def scoped_treasuries(req):
    rows = q(req.db, 'SELECT id,name,balance FROM treasuries WHERE active=1 ORDER BY name')
    comp = req.company or {}
    user = req.user or {}
    if comp.get('restrict_scope') and user.get('treasury_id') and not user.get('is_admin'):
        return [r for r in rows if r['id'] == user['treasury_id']] or rows[:1]
    return rows


def default_store(req):
    user = req.user or {}
    if user.get('store_id'):
        return user['store_id']
    rows = q(req.db, 'SELECT id FROM stores WHERE active=1 ORDER BY id LIMIT 1')
    return rows[0]['id'] if rows else 0


def default_treasury(req):
    user = req.user or {}
    if user.get('treasury_id'):
        return user['treasury_id']
    rows = q(req.db, 'SELECT id FROM treasuries WHERE active=1 ORDER BY id LIMIT 1')
    return rows[0]['id'] if rows else 0


# --------------------------------------------------------------------------- #
# pricing
# --------------------------------------------------------------------------- #
def active_offers(conn):
    d = today()
    rows = q(conn, 'SELECT * FROM offers WHERE active=1 AND (start_date=? OR start_date<=?) '
                   'AND (end_date=? OR end_date>=?)', ('', d, '', d))
    offers = {}
    for row in rows:
        if row.get('scope') == 'all' or not row.get('item_id'):
            offers['all'] = float(row.get('discount_percent') or 0)
        else:
            offers[row['item_id']] = float(row.get('discount_percent') or 0)
    return offers


def customer_tier_percent(conn, customer_id):
    if not customer_id:
        return 0.0
    row = one(conn, 'SELECT t.percent p FROM customers c JOIN price_tiers t ON t.id=c.tier_id '
                    'WHERE c.id=?', (customer_id,))
    try:
        return float(row['p']) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_price(base_price, tier_percent=0, offer_percent=0):
    try:
        price = float(base_price or 0)
    except (TypeError, ValueError):
        price = 0.0
    price = price * (1 - float(tier_percent or 0) / 100.0)
    price = price * (1 - float(offer_percent or 0) / 100.0)
    return round(price, 2)


# --------------------------------------------------------------------------- #
# pagination / filters
# --------------------------------------------------------------------------- #
def page_params(req, default_per=50):
    try:
        per = int(req.q('per') or default_per)
    except (TypeError, ValueError):
        per = default_per
    per = max(10, min(per, 500))
    try:
        page = int(req.q('page') or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    return page, per, (page - 1) * per


def pager(req, total, page, per):
    pages = max(1, (int(total) + per - 1) // per)

    def link(target):
        args = {k: v[0] for k, v in req.query.items() if k != 'page'}
        args['page'] = target
        import urllib.parse
        return req.path + '?' + urllib.parse.urlencode(args)

    return {'total': total, 'page': page, 'per': per, 'pages': pages,
            'prev': link(page - 1) if page > 1 else '', 'next': link(page + 1) if page < pages else '',
            'first': link(1), 'last': link(pages)}


def date_range(req, default_days=30):
    d2 = req.q('d2') or today()
    d1 = req.q('d1')
    if not d1:
        try:
            d1 = (datetime.date.fromisoformat(d2) - datetime.timedelta(days=default_days)).isoformat()
        except ValueError:
            d1 = today()
    return d1, d2


# --------------------------------------------------------------------------- #
# exports
# --------------------------------------------------------------------------- #
def export_rows(filename, rows, sheet='Sheet1', money_cols=None):
    data = xlsx.write_xlsx(rows, sheet_name=sheet, money_columns=money_cols or [])
    return file_response(
        data, filename if filename.lower().endswith('.xlsx') else filename + '.xlsx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def audit_rows(conn, limit=200):
    return q(conn, 'SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (limit,))
