# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - authentication, dashboard, settings, backups, admin."""
import json
import os
import platform
import re
import time
import urllib.parse

import core
import db
import gdrive
import i18n
import license as license_lib
import telegram_bot
from core import (T, audit, can, current_lang, flash, guard, json_error, json_ok, money,
                  next_number, q, render, one, scalar, today, x)
from db import ex
from framework import (Response, file_response, hash_password, redirect, router, url_for,
                       read_upload, check_password)
from crud import CRUDS


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@router.route('login', '/login', ('GET', 'POST'))
def login(req):
    if req.user:
        return redirect(url_for('dashboard'), req)
    if req.method == 'POST':
        user = one(req.db, 'SELECT * FROM users WHERE username=? AND active=1',
                   ((req.f('username') or '').strip(),))
        if user and check_password(req.f('password'), user['password']):
            req.session['uid'] = user['id']
            req.session.pop('lang', None)
            if user.get('lang'):
                req.session['lang'] = user['lang']
            ex(req.db, 'UPDATE users SET last_login=? WHERE id=?', (time.time(), user['id']))
            if not user.get('is_admin') and not user.get('trial_started'):
                license_lib.start_trial(req.db, user['id'])
                user['trial_started'] = time.time()
            req.user = user
            audit(req, 'login', 'users', user['id'])
            return redirect(url_for('dashboard'), req)
        audit(req, 'login_failed', 'users', 0, (req.f('username') or '').strip()[:40])
        flash(req, T(req, 'bad_credentials'), 'danger')
    return render(req, 'login.html', title=T(req, 'login'), login_page=True)


@router.route('logout', '/logout', ('GET', 'POST'))
def logout(req):
    req.session.clear()
    return redirect(url_for('login'), req)


def _safe_next(req, fallback_route='dashboard'):
    candidate = req.q('next') or req.environ.get('HTTP_REFERER') or url_for(fallback_route)
    if not candidate.startswith('/') or candidate.startswith('//'):
        return url_for(fallback_route)
    return candidate


@router.route('set_lang', '/lang/<code>', ('GET',))
def set_lang(req, code):
    req.session['lang'] = 'en' if code == 'en' else 'ar'
    if req.user:
        ex(req.db, 'UPDATE users SET lang=? WHERE id=?', (req.session['lang'], req.user['id']))
    return redirect(_safe_next(req, 'login' if not req.user else 'dashboard'), req)


@router.route('set_theme', '/theme/<name>', ('GET',))
def set_theme(req, name):
    req.session['theme'] = 'dark' if name == 'dark' else 'light'
    return redirect(_safe_next(req, 'login' if not req.user else 'dashboard'), req)


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
def _alerts(req):
    conn = req.db
    alerts = []
    expiry_window = int(req.company.get('expiry_alert_days') or 30)
    soon = db.add_period(today(), 'day', expiry_window)
    for row in q(conn, '''SELECT i.name n, COALESCE(SUM(m.qty),0) qty FROM items i
                          LEFT JOIN stock_movements m ON m.item_id=i.id
                          WHERE i.active=1 AND i.reorder_level>0
                          GROUP BY i.id, i.name, i.reorder_level HAVING COALESCE(SUM(m.qty),0) <= i.reorder_level'''):
        alerts.append({'icon': 'alert', 'tone': 'warn', 'route': 'report_reorder',
                       'text': T(req, 'alert_reorder', name=row['n'], qty=core.num(row['qty']))})
    for row in q(conn, '''SELECT i.name n, b.qty qty, b.expiry_date e FROM item_batches b
                          JOIN items i ON i.id=b.item_id
                          WHERE b.qty>0 AND b.expiry_date!='' AND b.expiry_date<=?
                          ORDER BY b.expiry_date LIMIT 8''', (soon,)):
        alerts.append({'icon': 'clock', 'tone': 'warn', 'route': 'report_expiry',
                       'text': T(req, 'alert_expiry', name=row['n'], date=row['e'],
                                 qty=core.num(row['qty']))})
    for row in q(conn, "SELECT number, otype FROM doc_orders WHERE due_date!='' AND due_date<? "
                       "AND status='pending' ORDER BY due_date LIMIT 6", (today(),)):
        alerts.append({'icon': 'clipboard', 'tone': 'danger',
                       'route': 'sale_orders' if row['otype'] == 'sale' else 'purchase_orders',
                       'text': T(req, 'alert_order_late',
                                 type=T(req, 'nav.sales' if row['otype'] == 'sale' else 'nav.purchases'),
                                 number=row['number'])})
    for row in q(conn, '''SELECT c.name n, i.amount a, i.due_date d FROM installments i
                          JOIN installment_plans p ON p.id=i.plan_id JOIN customers c ON c.id=p.customer_id
                          WHERE i.paid=0 AND i.due_date<? ORDER BY i.due_date LIMIT 6''', (today(),)):
        alerts.append({'icon': 'calendar', 'tone': 'danger', 'route': 'installment_due',
                       'text': T(req, 'alert_installment_late', name=row['n'],
                                 amount=core.money(row['a']), date=row['d'])})
    for row in q(conn, 'SELECT title, rdate FROM reminders WHERE done=0 AND rdate<=? '
                       'ORDER BY rdate LIMIT 6', (today(),)):
        alerts.append({'icon': 'bell', 'tone': 'info', 'route': 'reminders',
                       'text': T(req, 'alert_reminder', title=row['title'], date=row['rdate'])})
    for row in q(conn, "SELECT number, amount, due_date FROM cheques WHERE status='pending' "
                       "AND due_date<=? ORDER BY due_date LIMIT 5",
                 (db.add_period(today(), 'day', 7),)):
        alerts.append({'icon': 'credit-card', 'tone': 'warn', 'route': 'cheques',
                       'text': T(req, 'alert_cheque', number=row['number'],
                                 amount=core.money(row['amount']), date=row['due_date'])})
    for row in q(conn, 'SELECT name, balance, credit_limit FROM customers '
                       'WHERE credit_limit>0 AND balance>credit_limit ORDER BY balance DESC LIMIT 5'):
        alerts.append({'icon': 'users', 'tone': 'danger', 'route': 'customers',
                       'text': T(req, 'alert_credit_limit', name=row['name'],
                                 limit=core.money(row['credit_limit']))})
    return alerts


def _sales_trend(req, days=7):
    start = db.add_period(today(), 'day', -(days - 1))
    rows = {r['d']: r['v'] for r in q(
        req.db, "SELECT idate d, COALESCE(SUM(net),0) v FROM invoices "
                "WHERE itype='sale' AND status!='cancelled' AND idate>=? GROUP BY idate", (start,))}
    series = []
    for offset in range(days - 1, -1, -1):
        day = db.add_period(today(), 'day', -offset)
        series.append({'date': day, 'value': float(rows.get(day, 0) or 0)})
    peak = max([s['value'] for s in series] or [1]) or 1
    for item in series:
        item['pct'] = round(item['value'] * 100 / peak)
        item['label'] = item['date'][5:]
    return series


@router.route('dashboard', '/', ('GET',))
def dashboard(req):
    blocked = guard(req)
    if blocked:
        return blocked
    conn = req.db
    day = today()

    def card(label, value, route, kind='money', hint=''):
        return {'label': label, 'value': value, 'route': route, 'kind': kind, 'hint': hint}

    sales_today = scalar(conn, "SELECT COALESCE(SUM(net),0) v FROM invoices WHERE itype='sale' "
                               "AND idate=? AND status!='cancelled'", (day,))
    purchases_today = scalar(conn, "SELECT COALESCE(SUM(net),0) v FROM invoices WHERE itype='purchase' "
                                   "AND idate=? AND status!='cancelled'", (day,))
    cash = scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM treasuries')
    cards = [
        card('sales_today', sales_today, 'sale_invoices'),
        card('purchases_today', purchases_today, 'purchase_invoices'),
        card('cash_balance', cash, 'treasuries'),
        card('receivables', scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM customers WHERE balance>0'),
             'report_aging'),
        card('payables', scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM suppliers WHERE balance>0'),
             'report_aging'),
        card('inventory_value', scalar(conn, """SELECT COALESCE(SUM(m.qty * i.purchase_price),0) v
                                                FROM stock_movements m JOIN items i ON i.id=m.item_id"""),
             'report_stock'),
        card('customers_count', scalar(conn, 'SELECT COUNT(*) v FROM customers'), 'customers', 'int'),
        card('open_credit', scalar(conn, "SELECT COUNT(*) v FROM invoices WHERE status IN ('credit','partial') "
                                         "AND itype='sale'"), 'sale_invoices', 'int'),
    ]
    top = q(conn, """SELECT i.name n, SUM(ii.qty * COALESCE((SELECT factor FROM item_units u WHERE u.id=ii.unit_id),1)) tq,
                            SUM(ii.total) total
                     FROM invoice_items ii JOIN invoices v ON v.id=ii.invoice_id
                     JOIN items i ON i.id=ii.item_id
                     WHERE v.itype='sale' AND v.status!='cancelled'
                     GROUP BY i.id, i.name ORDER BY tq DESC LIMIT 6""")
    peak = max([float(r['tq'] or 0) for r in top] or [1]) or 1
    for row in top:
        row['pct'] = round(float(row['tq'] or 0) * 100 / peak)
    recent = q(conn, """SELECT v.*, COALESCE(c.name, s.name) party FROM invoices v
                        LEFT JOIN customers c ON c.id=v.party_id AND v.party_type='customer'
                        LEFT JOIN suppliers s ON s.id=v.party_id AND v.party_type='supplier'
                        ORDER BY v.id DESC LIMIT 8""")
    return render(req, 'dashboard.html', title=T(req, 'dashboard'), active='dashboard',
                  cards=cards, alerts=_alerts(req), top=top, recent=recent,
                  trend=_sales_trend(req), ask_demo=scalar(conn, 'SELECT COUNT(*) v FROM items') == 0)


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
SETTINGS_FIELDS = [
    'name', 'phone', 'address', 'email', 'tax_number', 'cr_number', 'currency', 'company_type',
    'lang', 'theme', 'accent', 'inv_prefix', 'inv_suffix', 'pinv_prefix', 'pinv_suffix',
    'order_prefix', 'vch_prefix', 'invoice_terms', 'receipt_footer',
]
SETTINGS_NUMBERS = ['next_sale_no', 'next_purchase_no', 'next_order_no', 'next_voucher_no',
                    'tax_percent', 'expiry_alert_days', 'backup_hours']
SETTINGS_FLAGS = ['auto_backup', 'restrict_scope', 'show_cost', 'gdrive_enabled']


@router.route('settings', '/settings', ('GET', 'POST'))
def settings(req):
    blocked = guard(req, 'p_settings')
    if blocked:
        return blocked
    if req.method == 'POST':
        data = {key: (req.f(key) or '').strip() for key in SETTINGS_FIELDS}
        for key in SETTINGS_NUMBERS:
            data[key] = core.safe_float(req.f(key))
        for key in SETTINGS_FLAGS:
            data[key] = 1 if req.f(key) else 0
        for key in ('gdrive_client_id', 'gdrive_client_secret', 'gdrive_folder_id'):
            if req.has(key):
                data[key] = (req.f(key) or '').strip()
        logo = read_upload(req.files.get('logo'), db.UPLOAD_DIR, 'logo')
        if logo:
            data['logo'] = logo
        if req.f('remove_logo'):
            data['logo'] = ''
        columns = ', '.join(f'{k}=?' for k in data)
        ex(req.db, f'UPDATE company SET {columns} WHERE id=1', tuple(data.values()))
        req.session['lang'] = data['lang'] if data['lang'] in ('ar', 'en') else 'ar'
        req.session['theme'] = data['theme'] if data['theme'] in ('light', 'dark') else 'light'
        audit(req, 'update', 'company', 1)
        flash(req, T(req, 'settings_saved'), 'success')
        return redirect(url_for('settings') + '#company', req)
    company = one(req.db, 'SELECT * FROM company WHERE id=1')
    return render(req, 'settings.html', title=T(req, 'settings'), active='settings',
                  comp=company, backups=db.backup_files()[:10])


# --------------------------------------------------------------------------- #
# backups
# --------------------------------------------------------------------------- #
@router.route('backup_restore', '/backup', ('GET',))
def backup_restore(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    company = req.company
    drive_files, drive_error = [], ''
    if company.get('gdrive_enabled') and company.get('gdrive_refresh_token'):
        try:
            drive_files = gdrive.list_backups(company['gdrive_client_id'],
                                              company['gdrive_client_secret'],
                                              company['gdrive_refresh_token'],
                                              company.get('gdrive_folder_id') or '')
        except Exception as exc:
            drive_error = str(exc)[:300]
    return render(req, 'backup.html', title=T(req, 'settings_backup'), active='backup_restore',
                  backups=db.backup_files(), drive_files=drive_files, drive_error=drive_error,
                  connected=bool(company.get('gdrive_refresh_token')),
                  redirect_uri=gdrive_redirect_uri(req))


@router.route('backup_now', '/backup/now', ('POST',))
def backup_now(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    try:
        result = db.backup_now(upload_to_drive=bool(req.company.get('gdrive_enabled')
                                                    and req.company.get('gdrive_refresh_token')))
        message = T(req, 'backup_created', name=result['name'])
        if result.get('drive') and result['drive'].get('ok'):
            flash(req, message + ' · ' + T(req, 'gdrive_synced'), 'success')
        else:
            flash(req, message, 'success')
    except Exception as exc:
        flash(req, str(exc), 'danger')
    return redirect(url_for('backup_restore'), req)


@router.route('backup_download', '/backup/download/<name>', ('GET',))
def backup_download(req, name):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    name = os.path.basename(name)
    path = os.path.join(db.BACKUP_DIR, name)
    if not os.path.isfile(path):
        return core.not_found()
    return file_response(open(path, 'rb').read(), name, 'application/octet-stream')


@router.route('backup_delete', '/backup/delete', ('POST',))
def backup_delete(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    name = os.path.basename(req.f('name') or '')
    path = os.path.join(db.BACKUP_DIR, name)
    if name and os.path.isfile(path):
        try:
            os.remove(path)
            flash(req, T(req, 'deleted'), 'warning')
        except OSError as exc:
            flash(req, str(exc), 'danger')
    return redirect(url_for('backup_restore'), req)


@router.route('backup_restore_do', '/backup/restore', ('POST',))
def backup_restore_do(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    upload = req.files.get('file')
    chosen = req.f('existing')
    path = None
    if upload and upload.get('data'):
        temp = os.path.join(db.BACKUP_DIR, 'incoming-restore.db')
        with open(temp, 'wb') as fh:
            fh.write(upload['data'])
        path = temp
    elif chosen:
        path = os.path.join(db.BACKUP_DIR, os.path.basename(chosen))
    if not path or not os.path.isfile(path):
        flash(req, T(req, 'restore_bad_file'), 'danger')
        return redirect(url_for('backup_restore'), req)
    try:
        try:
            req.db.close()
        except Exception:
            pass
        db.restore_backup(path)
        db.init_db()
        req.session.clear()
        flash(req, T(req, 'restore_done'), 'success')
        return redirect(url_for('login'), req)
    except Exception as exc:
        flash(req, T(req, 'restore_bad_file') + f' ({str(exc)[:120]})', 'danger')
        return redirect(url_for('backup_restore'), req)


def gdrive_redirect_uri(req):
    scheme = req.environ.get('HTTP_X_FORWARDED_PROTO') or ('https' if req.environ.get('HTTPS') else 'http')
    host = req.environ.get('HTTP_HOST') or 'localhost:8000'
    return f'{scheme}://{host}/gdrive/callback'


@router.route('gdrive_connect', '/gdrive/connect', ('GET',))
def gdrive_connect(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    company = req.company
    if not (company.get('gdrive_client_id') and company.get('gdrive_client_secret')):
        flash(req, T(req, 'gdrive_need_creds'), 'danger')
        return redirect(url_for('backup_restore'), req)
    url = gdrive.authorize_url(company['gdrive_client_id'], gdrive_redirect_uri(req))
    return redirect(url, req)


@router.route('gdrive_callback', '/gdrive/callback', ('GET',))
def gdrive_callback(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    code = req.q('code')
    company = req.company
    if not code:
        flash(req, T(req, 'gdrive_offline'), 'danger')
        return redirect(url_for('backup_restore'), req)
    try:
        data = gdrive.exchange_code(code, company['gdrive_client_id'],
                                    company['gdrive_client_secret'], gdrive_redirect_uri(req))
        email = gdrive.account_email(data['access_token'])
        folder = ''
        try:
            folder = gdrive.ensure_folder(company['gdrive_client_id'], company['gdrive_client_secret'],
                                          data['refresh_token']) or ''
        except Exception:
            folder = ''
        ex(req.db, 'UPDATE company SET gdrive_refresh_token=?, gdrive_enabled=1, gdrive_email=?, '
                   'gdrive_folder_id=? WHERE id=1',
           (data['refresh_token'], email, folder))
        flash(req, T(req, 'gdrive_connected') + (f' — {email}' if email else ''), 'success')
    except Exception as exc:
        flash(req, T(req, 'gdrive_failed', msg=str(exc)[:200]), 'danger')
    return redirect(url_for('backup_restore'), req)


@router.route('gdrive_disconnect', '/gdrive/disconnect', ('POST',))
def gdrive_disconnect(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    ex(req.db, "UPDATE company SET gdrive_refresh_token='', gdrive_enabled=0, gdrive_email='' WHERE id=1")
    flash(req, T(req, 'deleted'), 'warning')
    return redirect(url_for('backup_restore'), req)


@router.route('gdrive_upload', '/gdrive/upload', ('POST',))
def gdrive_upload(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    company = req.company
    if not company.get('gdrive_refresh_token'):
        flash(req, T(req, 'gdrive_need_creds'), 'danger')
        return redirect(url_for('backup_restore'), req)
    try:
        result = db.backup_now(upload_to_drive=False)
        info = gdrive.upload(result['path'], result['name'], company['gdrive_client_id'],
                             company['gdrive_client_secret'], company['gdrive_refresh_token'],
                             company.get('gdrive_folder_id') or '')
        ex(req.db, 'UPDATE company SET gdrive_last_sync=? WHERE id=1', (time.time(),))
        flash(req, T(req, 'gdrive_synced') + f" · {info.get('name') or result['name']}", 'success')
    except Exception as exc:
        flash(req, T(req, 'gdrive_failed', msg=str(exc)[:200]), 'danger')
    return redirect(url_for('backup_restore'), req)


@router.route('gdrive_restore', '/gdrive/restore/<fid>', ('POST',))
def gdrive_restore(req, fid):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    company = req.company
    try:
        payload = gdrive.download(fid, company['gdrive_client_id'], company['gdrive_client_secret'],
                                  company['gdrive_refresh_token'])
        temp = os.path.join(db.BACKUP_DIR, 'drive-restore.db')
        with open(temp, 'wb') as fh:
            fh.write(payload)
        try:
            req.db.close()
        except Exception:
            pass
        db.restore_backup(temp)
        db.init_db()
        req.session.clear()
        flash(req, T(req, 'restore_done'), 'success')
        return redirect(url_for('login'), req)
    except Exception as exc:
        flash(req, T(req, 'gdrive_failed', msg=str(exc)[:200]), 'danger')
        return redirect(url_for('backup_restore'), req)


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #
@router.route('profile', '/profile', ('GET', 'POST'))
def profile(req):
    blocked = guard(req)
    if blocked:
        return blocked
    if req.method == 'POST':
        data = {
            'full_name': (req.f('full_name') or '').strip(),
            'phone': (req.f('phone') or '').strip(),
            'email': (req.f('email') or '').strip(),
            'lang': req.f('lang') if req.f('lang') in ('ar', 'en') else '',
        }
        new_pass = req.f('new_password') or ''
        if new_pass:
            if not check_password(req.f('current_password'), req.user['password']):
                flash(req, T(req, 'bad_credentials'), 'danger')
                return redirect(url_for('profile'), req)
            data['password'] = hash_password(new_pass)
        columns = ', '.join(f'{k}=?' for k in data)
        ex(req.db, f'UPDATE users SET {columns} WHERE id=?', tuple(data.values()) + (req.user['id'],))
        if data['lang']:
            req.session['lang'] = data['lang']
        flash(req, T(req, 'saved'), 'success')
        return redirect(url_for('profile'), req)
    return render(req, 'profile.html', title=T(req, 'profile'), active='profile')


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def _search_entities(req, term):
    like = f'%{term}%'
    results = {'items': q(req.db, "SELECT id, code, name FROM items WHERE name LIKE ? OR code LIKE ? "
                                  "ORDER BY name LIMIT 8", (like, like)),
               'customers': q(req.db, 'SELECT id, name, phone FROM customers WHERE name LIKE ? OR phone LIKE ? '
                                      'ORDER BY name LIMIT 6', (like, like)),
               'suppliers': q(req.db, 'SELECT id, name, phone FROM suppliers WHERE name LIKE ? OR phone LIKE ? '
                                      'ORDER BY name LIMIT 6', (like, like)),
               'invoices': q(req.db, 'SELECT id, number, itype, net FROM invoices WHERE number LIKE ? '
                                     'ORDER BY id DESC LIMIT 6', (like,)),
               'orders': q(req.db, 'SELECT id, number, otype FROM doc_orders WHERE number LIKE ? '
                                   'ORDER BY id DESC LIMIT 5', (like,))}
    return results


@router.route('search', '/search', ('GET',))
def search(req):
    blocked = guard(req)
    if blocked:
        return blocked
    term = (req.q('q') or '').strip()
    results = _search_entities(req, term) if term else {}
    return render(req, 'search.html', title=T(req, 'search_everything'), active='search',
                  term=term, results=results)


@router.route('api_search', '/api/search', ('GET',))
def api_search(req):
    if not req.user:
        return json_error('unauthorized', '401 Unauthorized')
    term = (req.q('q') or '').strip()
    nav = []
    if term:
        lang = current_lang(req)
        for group in core.build_nav(req):
            for item in group['items']:
                label = i18n.t(item['label'], lang)
                if term.lower() in label.lower():
                    nav.append({'label': label, 'href': item['href'], 'group': i18n.t(group['label'], lang)})
    entities = _search_entities(req, term) if term else {}
    payload = {'nav': nav[:8], 'items': [], 'parties': [], 'invoices': []}
    for item in entities.get('items', []):
        payload['items'].append({'label': f"{item['name']} · {item['code'] or ''}".strip(' ·'),
                                 'href': url_for('items') + '?s=' + urllib.parse.quote(item['code'] or item['name'])})
    for customer in entities.get('customers', []):
        payload['parties'].append({'label': customer['name'], 'href': url_for('customers') + '?s=' + urllib.parse.quote(customer['name'])})
    for supplier in entities.get('suppliers', []):
        payload['parties'].append({'label': supplier['name'], 'href': url_for('suppliers') + '?s=' + urllib.parse.quote(supplier['name'])})
    for invoice in entities.get('invoices', []):
        payload['invoices'].append({'label': f"{invoice['number']} · {core.money(invoice['net'])}",
                                    'href': url_for('invoice_view', iid=invoice['id'])})
    return json_ok(payload)


# --------------------------------------------------------------------------- #
# audit / system
# --------------------------------------------------------------------------- #
@router.route('audit', '/audit', ('GET',))
def audit_page(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    page, per, offset = core.page_params(req, 60)
    total = scalar(req.db, 'SELECT COUNT(*) c FROM audit_log')
    rows = q(req.db, 'SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?', (per, offset))
    return render(req, 'audit.html', title=T(req, 'audit_log'), active='audit', rows=rows,
                  pager=core.pager(req, total, page, per), total=total)


@router.route('system_info', '/system', ('GET',))
def system_info(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    counts = {}
    for table in ('items', 'customers', 'suppliers', 'invoices', 'doc_orders', 'treasuries', 'stores',
                  'vouchers', 'employees', 'users', 'journal_entries' if False else 'journals'):
        counts[table] = scalar(req.db, f'SELECT COUNT(*) c FROM {table}')
    info = {
        'app': core.APP_NAME,
        'version': '3.0',
        'python': platform.python_version(),
        'platform': platform.platform(),
        'engine': 'Microsoft SQL Server' if db.DIALECT == 'mssql' else 'SQLite',
        'db_path': db.DB_PATH,
        'backup_dir': db.BACKUP_DIR,
        'backup_count': len(db.backup_files()),
        'schema': db.SCHEMA_VERSION,
    }
    return render(req, 'system.html', title=T(req, 'system_info'), active='system_info',
                  info=info, counts=counts)


# --------------------------------------------------------------------------- #
# demo data
# --------------------------------------------------------------------------- #
DEMO_MARK = '[DEMO]'


@router.route('demo_load', '/demo/load', ('POST',))
def demo_load(req):
    blocked = guard(req, 'p_settings')
    if blocked:
        return blocked
    conn = req.db
    stamp = int(time.time())
    tier = one(conn, 'SELECT id FROM price_tiers ORDER BY id LIMIT 1')
    tier_id = tier['id'] if tier else 0
    store = one(conn, 'SELECT id FROM stores ORDER BY id LIMIT 1')
    cash = one(conn, 'SELECT id FROM treasuries ORDER BY id LIMIT 1')
    store_id = store['id'] if store else 0
    cash_id = cash['id'] if cash else 0
    if cash_id:
        ex(conn, 'UPDATE treasuries SET balance=balance+5000 WHERE id=?', (cash_id,))
    customer_id = x(conn, 'INSERT INTO customers(name,phone,address,tier_id,credit_limit,balance,active,notes) '
                          'VALUES(?,?,?,?,?,?,?,?)',
                    ('عميل تجريبي', '01000000000', 'القاهرة', tier_id, 5000, 0, 1, DEMO_MARK))
    supplier_id = x(conn, 'INSERT INTO suppliers(name,phone,address,balance,active,notes) '
                          'VALUES(?,?,?,?,?,?)', ('مورد تجريبي', '01100000000', 'الجيزة', 0, 1, DEMO_MARK))
    demo_items = [('DM-001', 'شاي معبأ 250 جم', 'بقالة', 18, 25, 10, 0),
                  ('DM-002', 'سكر 1 كجم', 'بقالة', 22, 30, 10, 0),
                  ('DM-003', 'عصير برتقال 1 لتر', 'مشروبات', 9, 15, 20, 1)]
    item_rows = []
    for code, name, category, cost, price, reorder, has_expiry in demo_items:
        item_id = x(conn, 'INSERT INTO items(code,name,category,purchase_price,sale_price,reorder_level,'
                          'has_expiry,active,notes) VALUES(?,?,?,?,?,?,?,?,?)',
                    (code, name, category, cost, price, reorder, has_expiry, 1, DEMO_MARK))
        x(conn, 'INSERT INTO item_units(item_id,name,factor,barcode,sale_price,purchase_price,is_base) '
               'VALUES(?,?,?,?,?,?,?)', (item_id, 'قطعة', 1, f'{stamp}{len(item_rows)}', price, cost, 1))
        x(conn, 'INSERT INTO item_units(item_id,name,factor,barcode,sale_price,purchase_price) '
               'VALUES(?,?,?,?,?,?)', (item_id, 'كرتونة', 12, f'{stamp}9{len(item_rows)}',
                                       round(price * 11, 2), round(cost * 11, 2)))
        item_rows.append((item_id, code, name, cost, price))
    # opening stock
    for item_id, code, name, cost, price in item_rows:
        unit = one(conn, 'SELECT id FROM item_units WHERE item_id=? ORDER BY factor LIMIT 1', (item_id,))
        core.movement(conn, item_id, unit['id'] if unit else 0, store_id, 100, 'opening', 0,
                      'Demo opening stock', today(), req.user['id'])
    # a purchase invoice
    inv_no = next_number(req, 'purchase')
    total = sum(cost * 20 for _, _, _, cost, _ in item_rows)
    invoice_id = x(conn, 'INSERT INTO invoices(number,itype,idate,party_type,party_id,store_id,treasury_id,'
                         'subtotal,discount,tax,net,paid,status,notes,user_id,is_tax,ctime,ref) '
                         'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (inv_no, 'purchase', today(), 'supplier', supplier_id, store_id, cash_id,
                    total, 0, 0, total, total, 'paid', DEMO_MARK, req.user['id'], 0, time.time(), DEMO_MARK))
    for item_id, code, name, cost, price in item_rows:
        unit = one(conn, 'SELECT id FROM item_units WHERE item_id=? ORDER BY factor LIMIT 1', (item_id,))
        x(conn, 'INSERT INTO invoice_items(invoice_id,item_id,unit_id,qty,price,cost,total) '
               'VALUES(?,?,?,?,?,?,?)', (invoice_id, item_id, unit['id'], 20, cost, cost, cost * 20))
        core.movement(conn, item_id, unit['id'], store_id, 20, 'purchase_invoice', invoice_id, inv_no,
                      today(), req.user['id'])
    core.treasury_delta(conn, cash_id, -total)
    # a sale invoice
    sale_no = next_number(req, 'sale')
    lines = []
    subtotal = 0
    for item_id, code, name, cost, price in item_rows[:2]:
        unit = one(conn, 'SELECT id FROM item_units WHERE item_id=? ORDER BY factor DESC LIMIT 1', (item_id,))
        qty, sell = 2, price
        subtotal += qty * sell
        lines.append((item_id, unit['id'], qty, sell, cost, qty * sell))
    tax = round(subtotal * core.safe_float(req.company.get('tax_percent')) / 100, 2)
    net = round(subtotal + tax, 2)
    sale_id = x(conn, 'INSERT INTO invoices(number,itype,idate,party_type,party_id,store_id,treasury_id,'
                      'subtotal,discount,tax,tax_percent,net,paid,status,notes,user_id,is_tax,ctime,ref) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (sale_no, 'sale', today(), 'customer', customer_id, store_id, cash_id, subtotal, 0, tax,
                 core.safe_float(req.company.get('tax_percent')), net, round(net / 2, 2), 'partial',
                 DEMO_MARK, req.user['id'], 1, time.time(), DEMO_MARK))
    for item_id, unit_id, qty, sell, cost, line_total in lines:
        x(conn, 'INSERT INTO invoice_items(invoice_id,item_id,unit_id,qty,price,cost,total) '
               'VALUES(?,?,?,?,?,?,?)', (sale_id, item_id, unit_id, qty, sell, cost, line_total))
        core.movement(conn, item_id, unit_id, store_id, -qty * core.unit_factor(conn, unit_id),
                      'sale_invoice', sale_id, sale_no, today(), req.user['id'])
        core.consume_batches(conn, item_id, store_id, qty * core.unit_factor(conn, unit_id))
    core.treasury_delta(conn, cash_id, round(net / 2, 2))
    core.party_delta(conn, 'customer', customer_id, round(net / 2, 2))
    audit(req, 'create', 'demo', 0, 'demo data loaded')
    flash(req, T(req, 'demo_loaded'), 'success')
    return redirect(url_for('dashboard'), req)


@router.route('demo_clear', '/demo/clear', ('POST',))
def demo_clear(req):
    blocked = guard(req, 'p_settings')
    if blocked:
        return blocked
    conn = req.db
    demo_invoices = [str(row['id']) for row in q(conn, 'SELECT id FROM invoices WHERE ref=?', (DEMO_MARK,))]
    demo_items = [str(row['id']) for row in q(conn, 'SELECT id FROM items WHERE notes=?', (DEMO_MARK,))]
    if demo_invoices:
        marks = ','.join(demo_invoices)
        ex(conn, f'DELETE FROM stock_movements WHERE ref_id IN ({marks}) '
                 f"AND ref_type IN ('sale_invoice','purchase_invoice')")
        ex(conn, f'DELETE FROM invoice_items WHERE invoice_id IN ({marks})')
        ex(conn, f'DELETE FROM invoices WHERE id IN ({marks})')
    if demo_items:
        marks = ','.join(demo_items)
        ex(conn, f'DELETE FROM stock_movements WHERE item_id IN ({marks})')
        ex(conn, f'DELETE FROM item_batches WHERE item_id IN ({marks})')
        ex(conn, f'DELETE FROM item_units WHERE item_id IN ({marks})')
        ex(conn, f'DELETE FROM items WHERE id IN ({marks})')
    for table in ('customers', 'suppliers'):
        demo = [str(row['id']) for row in q(conn, f'SELECT id FROM {table} WHERE notes=?', (DEMO_MARK,))]
        if demo:
            marks = ','.join(demo)
            ex(conn, f'DELETE FROM {table} WHERE id IN ({marks})')
    audit(req, 'delete', 'demo', 0, 'demo data cleared')
    flash(req, T(req, 'demo_cleared'), 'warning')
    return redirect(url_for('dashboard'), req)





@router.route('signup', '/signup', ('GET', 'POST'))
def signup(req):
    """Self registration: the account is active immediately with a 14-day trial.

    A plan chosen on the form is stored as a request; the administrator
    activates it (and renews it later) from the Licence screen.
    """
    if req.user:
        return redirect(url_for('dashboard'), req)
    allowed = int(req.company.get('allow_signup') or 0) == 1
    error = ''
    form = {key: req.f(key) for key in ('full_name', 'username', 'phone', 'email')}
    selected_plan = req.f('plan') or 'trial'
    if selected_plan != 'trial' and not license_lib.plan(selected_plan):
        selected_plan = 'trial'
    if req.method == 'POST' and allowed:
        full_name = (req.f('full_name') or '').strip()
        username = (req.f('username') or '').strip().lower()
        phone = (req.f('phone') or '').strip()
        email = (req.f('email') or '').strip()
        password = req.f('password') or ''
        confirm = req.f('password2') or ''
        if not re.match(r'^[a-z0-9_.-]{3,32}$', username):
            error = T(req, 'signup_username_invalid')
        elif not full_name:
            error = T(req, 'required_field') + ': ' + T(req, 'full_name')
        elif len(password) < 6:
            error = T(req, 'signup_short_password')
        elif password != confirm:
            error = T(req, 'signup_mismatch')
        elif one(req.db, 'SELECT id FROM users WHERE username=?', (username,)):
            error = T(req, 'signup_username_taken')
        else:
            now = time.time()
            role_id = db.default_role_id(req.db)
            user_id = x(req.db, 'INSERT INTO users(username,password,full_name,phone,email,role_id,'
                                'is_admin,active,lang,created_at,trial_started,notes) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (username, hash_password(password), full_name, phone, email, role_id,
                         0, 1, '', now, now, 'signup'))
            if selected_plan != 'trial':
                license_lib.request_subscription(req.db, user_id, selected_plan, 'signup')
            audit(req, 'create', 'users', user_id,
                  f'signup: {username} · {selected_plan}')
            telegram_bot.notify_new_account(user_id,
                                            selected_plan if selected_plan != 'trial' else '')
            message = T(req, 'signup_ready_msg')
            if selected_plan != 'trial':
                info = license_lib.plan(selected_plan)
                message += ' ' + T(req, 'signup_plan_requested',
                                    plan=T(req, 'plan_' + selected_plan),
                                    price=f"{info['price']:,.0f}")
            flash(req, message, 'success')
            return redirect(url_for('login'), req)
    return render(req, 'signup.html', title=T(req, 'signup'), allowed=allowed,
                  error=error, form=form, plans=license_lib.plans(),
                  selected_plan=selected_plan, trial_days=license_lib.TRIAL_DAYS)



@router.route('healthz', '/healthz', ('GET',))
def healthz(req):
    """Public readiness probe for reverse proxies and containers."""
    try:
        users = scalar(req.db, 'SELECT COUNT(*) c FROM users')
        state = license_lib.state(req.db, req.company)
        return core.json_response({
            'ok': True,
            'app': core.APP_NAME,
            'version': core.VERSION,
            'engine': 'sqlserver' if db.DIALECT == 'mssql' else 'sqlite',
            'users': users,
            'licence': state['state'],
            'admin_lifetime': True,
            'time': int(time.time()),
        })
    except Exception as exc:  # pragma: no cover
        return core.json_response({'ok': False, 'error': str(exc)[:160]},
                                  '503 Service Unavailable')


# --------------------------------------------------------------------------- #
# licence generator (admin)
# --------------------------------------------------------------------------- #
@router.route('license', '/license', ('GET',))
def license_page(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    installation = license_lib.state(req.db, req.company)
    access = license_lib.user_state(req.db, req.user, req.company)
    generated = req.session.pop('generated_key', '')
    trials = license_lib.user_trials(req.db)
    trial_users = [row for row in trials if row['licence'].get('plan') == 'trial']
    return render(req, 'license.html', title=T(req, 'license'), subtitle=T(req, 'license_sub'),
                  active='license', lic=access, installation=installation,
                  trials=trials, trial_users=trial_users,
                  requests=license_lib.pending_requests(req.db),
                  plans=license_lib.plans(),
                  machine=license_lib.machine_id(),
                  history=license_lib.history(req.db), editions=license_lib.EDITIONS,
                  generated=generated, trial_days=license_lib.TRIAL_DAYS,
                  default_expires=db.add_period(today(), 'day', 365),
                  generated_payload=license_lib.parse(generated)[0] if generated else None)


@router.route('license_generate', '/license/generate', ('POST',))
def license_generate(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    customer = (req.f('customer') or '').strip()
    edition = req.f('edition') or 'standard'
    seats = max(1, core.safe_int(req.f('seats'), 1))
    days = max(1, core.safe_int(req.f('days'), 365))
    expires = (req.f('expires') or '').strip()
    bind = req.f('bind') or 'any'
    machine = license_lib.machine_id() if bind == 'machine' else ''
    if not customer:
        flash(req, T(req, 'required_field') + ': ' + T(req, 'license_customer'), 'danger')
        return redirect(url_for('license'), req)
    key = license_lib.generate(customer, edition, seats, days, expires, machine)
    req.session['generated_key'] = key
    audit(req, 'create', 'licenses', 0, f'generated {edition} {customer}')
    flash(req, T(req, 'license_generated'), 'success')
    return redirect(url_for('license'), req)


@router.route('license_activate', '/license/activate', ('POST',))
def license_activate(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    key = req.f('key') or req.session.get('generated_key', '')
    result = license_lib.activate(req.db, key, req.user['username'],
                                  allow_machine_mismatch=bool(req.f('force')))
    if result['ok']:
        audit(req, 'create', 'licenses', 0, 'activated')
        flash(req, T(req, 'license_activated'), 'success')
    else:
        reason = result['reason']
        message = {'signature': 'license_bad_signature', 'expired': 'license_bad_expired',
                   'machine': 'license_bad_machine', 'malformed': 'license_bad_format',
                   'edition': 'license_bad_format'}.get(reason, 'license_bad_format')
        flash(req, T(req, message), 'danger')
    return redirect(url_for('license'), req)


@router.route('license_revoke', '/license/revoke', ('POST',))
def license_revoke(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    license_id = req.i('id')
    if license_id:
        license_lib.revoke(req.db, license_id)
        audit(req, 'delete', 'licenses', license_id, 'revoked')
        flash(req, T(req, 'license_revoked'), 'warning')
    return redirect(url_for('license'), req)



@router.route('license_user_action', '/license/user', ('POST',))
def license_user_action(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    user_id = req.i('uid')
    action = req.f('action')
    target = one(req.db, 'SELECT * FROM users WHERE id=?', (user_id,)) if user_id else None
    if not target:
        flash(req, T(req, 'not_found'), 'danger')
        return redirect(url_for('license'), req)
    if target.get('is_admin'):
        flash(req, T(req, 'license_admin_untouchable'), 'warning')
        return redirect(url_for('license'), req)
    if action == 'reset':
        license_lib.reset_trial(req.db, user_id)
        audit(req, 'update', 'licenses', user_id, f'trial reset for {target["username"]}')
        flash(req, T(req, 'license_trial_reset', name=target['username']), 'success')
    elif action == 'renew':
        plan_key = req.f('plan')
        info = license_lib.plan(plan_key)
        if not info:
            flash(req, T(req, 'license_bad_format'), 'danger')
            return redirect(url_for('license'), req)
        subscription = license_lib.activate_subscription(req.db, user_id, plan_key,
                                                         req.user['username'])
        if not subscription:
            flash(req, T(req, 'license_already_lifetime'), 'warning')
            return redirect(url_for('license'), req)
        audit(req, 'update', 'licenses', user_id,
              f'{plan_key} activated/renewed for {target["username"]}')
        expires = T(req, 'license_lifetime_plan') if subscription['days_left'] is None \
            else (subscription['expires_iso'] or '')
        flash(req, T(req, 'license_renewed', name=target['username'],
                     plan=T(req, 'plan_' + plan_key), expires=expires), 'success')
    elif action == 'cancel':
        license_lib.cancel_subscription(req.db, user_id)
        audit(req, 'update', 'licenses', user_id, f'subscription cancelled for {target["username"]}')
        flash(req, T(req, 'license_subscription_cancelled', name=target['username']), 'warning')
    elif action == 'grant':
        license_lib.set_user_licensed(req.db, user_id, True)
        audit(req, 'update', 'licenses', user_id, f'lifetime granted to {target["username"]}')
        flash(req, T(req, 'license_granted', name=target['username']), 'success')
    elif action == 'revoke':
        license_lib.set_user_licensed(req.db, user_id, False)
        license_lib.cancel_subscription(req.db, user_id)
        license_lib.reset_trial(req.db, user_id)
        audit(req, 'update', 'licenses', user_id, f'lifetime revoked from {target["username"]}')
        flash(req, T(req, 'license_revoked_user', name=target['username']), 'warning')
    return redirect(url_for('license'), req)


@router.route('license_share', '/license/share/<uid>', ('GET',))
def license_share(req, uid):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    target = one(req.db, 'SELECT * FROM users WHERE id=?', (core.safe_int(uid),))
    if not target:
        return core.not_found()
    payload = license_lib.share_text(req.db, target).encode('utf-8')
    filename = f"sanadex-licence-{target['username']}.txt"
    audit(req, 'read', 'licenses', target['id'], f'shared licence for {target["username"]}')
    return file_response(payload, filename, 'text/plain; charset=utf-8')


@router.route('license_telegram_detect', '/license/telegram/detect', ('POST',))
def license_telegram_detect(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    token = (req.f('telegram_token') or '').strip() or (req.company.get('telegram_token') or '')
    if not token:
        flash(req, T(req, 'telegram_missing'), 'danger')
        return redirect(url_for('license'), req)
    ex(req.db, 'UPDATE company SET telegram_token=? WHERE id=1', (token,))
    chats = telegram_bot.recent_chats(token)
    if not chats:
        flash(req, T(req, 'telegram_no_chats'), 'warning')
    elif len(chats) == 1:
        chat = chats[0]
        ex(req.db, 'UPDATE company SET telegram_chat_id=? WHERE id=1', (chat['id'],))
        flash(req, T(req, 'telegram_detected',
                     name=chat['name'] or chat['username'] or chat['id'], id=chat['id']), 'success')
    else:
        listing = ', '.join(f"{c['name'] or c['username']} ({c['id']})" for c in chats[:8])
        flash(req, T(req, 'telegram_detected_many', list=listing), 'warning')
    return redirect(url_for('license'), req)


@router.route('license_telegram', '/license/telegram', ('POST',))
def license_telegram(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    token = (req.f('telegram_token') or '').strip()
    chat_id = (req.f('telegram_chat_id') or '').strip()
    public_url = (req.f('public_url') or '').strip().rstrip('/')
    digest_hour = max(0, min(23, core.safe_int(req.f('telegram_digest_hour'), 21)))
    enabled = 1 if req.f('telegram_enabled') else 0
    ex(req.db, 'UPDATE company SET telegram_enabled=?, telegram_token=?, telegram_chat_id=?, '
               'telegram_digest_hour=?, public_url=? WHERE id=1',
       (enabled, token, chat_id, digest_hour, public_url))
    audit(req, 'update', 'company', 1, 'telegram bot settings')
    flash(req, T(req, 'telegram_saved'), 'success')
    return redirect(url_for('license'), req)


@router.route('license_telegram_test', '/license/telegram/test', ('POST',))
def license_telegram_test(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    cfg = telegram_bot.config()
    if not cfg.get('token') or not cfg.get('chat_ids'):
        flash(req, T(req, 'telegram_missing'), 'danger')
    elif telegram_bot.send(cfg, '✅ Sanadex.wanis.online — test message. '
                                f'The bot is connected to {req.user["username"]}.',
                           keyboard=telegram_bot.menu_keyboard(cfg)):
        flash(req, T(req, 'telegram_test_sent'), 'success')
    else:
        flash(req, T(req, 'telegram_test_failed'), 'danger')
    return redirect(url_for('license'), req)


@router.route('license_download', '/license/download', ('GET',))
def license_download(req):
    blocked = guard(req, 'admin')
    if blocked:
        return blocked
    current = license_lib.active_license(req.db)
    if not current:
        return core.not_found()
    payload = license_lib.license_text(current['key']).encode('utf-8')
    return file_response(payload, 'sanadex-license.txt', 'text/plain; charset=utf-8')
