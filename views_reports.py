# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - reports, statements and the accounting module."""
import datetime

import core
from core import (T, audit, can, date_range, export_rows, flash, guard, money, one, q, render,
                  scalar, today, x)
from db import ex
from framework import redirect, router, url_for


# --------------------------------------------------------------------------- #
# generic report plumbing
# --------------------------------------------------------------------------- #
def _cell(value, kind):
    if value is None:
        return ''
    if kind == 'money':
        return core.money(value)
    if kind == 'int':
        return core.num(value)
    if kind == 'pct':
        return core.pct(value)
    return value


def _export(columns, rows, totals, filename):
    header = [col['label'] for col in columns]
    data = [header]
    for row in rows:
        data.append([_cell(row.get(col['key']), col.get('kind')) for col in columns])
    if totals:
        data.append([_cell(totals.get(col['key']), col.get('kind')) if totals.get(col['key']) is not None
                     else '' for col in columns])
    return export_rows(filename, data)


def report(req, template, title_key, filename, columns, rows, totals=None, subtitle_key='',
           extra=None, http_status='200 OK'):
    if req.q('export'):
        return _export(columns, rows, totals, filename)
    return render(req, template, http_status=http_status,
                  title=T(req, title_key), subtitle=T(req, subtitle_key) if subtitle_key else '',
                  active=req.path.strip('/').replace('/', '_'), columns=columns, rows=rows,
                  totals=totals, extra=extra,
                  d1=req.q('d1') or '', d2=req.q('d2') or '',
                  show_dates=True)


def generic_report(req, title_key, filename, columns, rows, totals=None, subtitle_key='',
                   show_dates=True, notes=''):
    if req.q('export'):
        return _export(columns, rows, totals, filename)
    return render(req, 'report.html', title=T(req, title_key),
                  subtitle=T(req, subtitle_key) if subtitle_key else '', columns=columns, rows=rows,
                  totals=totals, active=req.path.strip('/').replace('/', '_'), show_dates=show_dates,
                  d1=req.q('d1') or '', d2=req.q('d2') or '', notes=notes)


# --------------------------------------------------------------------------- #
# profit reports
# --------------------------------------------------------------------------- #
@router.route('report_profit_item', '/reports/profit-item', ('GET',))
def report_profit_item(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    rows = q(req.db, '''SELECT i.code, i.name, SUM(ii.qty) qty, SUM(ii.total) revenue,
                               SUM(ii.qty * ii.cost) cost, SUM(ii.total - ii.qty * ii.cost) profit
                        FROM invoice_items ii JOIN invoices v ON v.id=ii.invoice_id
                        JOIN items i ON i.id=ii.item_id
                        WHERE v.itype='sale' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?
                        GROUP BY i.id, i.code, i.name ORDER BY profit DESC''', (d1, d2))
    for row in rows:
        revenue = core.safe_float(row['revenue'])
        row['margin'] = round(core.safe_float(row['profit']) * 100 / revenue, 1) if revenue else 0
    totals = {'name': T(req, 'total'), 'qty': sum(core.safe_float(r['qty']) for r in rows),
              'revenue': sum(core.safe_float(r['revenue']) for r in rows),
              'cost': sum(core.safe_float(r['cost']) for r in rows),
              'profit': sum(core.safe_float(r['profit']) for r in rows)}
    columns = [{'key': 'name', 'label': 'item'}, {'key': 'code', 'label': 'code'},
               {'key': 'qty', 'label': 'qty', 'kind': 'int', 'align': 'end'},
               {'key': 'revenue', 'label': 'revenue', 'kind': 'money', 'align': 'end'},
               {'key': 'cost', 'label': 'cost', 'kind': 'money', 'align': 'end'},
               {'key': 'profit', 'label': 'profit', 'kind': 'money', 'align': 'end'},
               {'key': 'margin', 'label': 'margin', 'kind': 'pct', 'align': 'end'}]
    return report(req, 'report.html', 'report_profit_item', 'profit-items.xlsx', columns, rows,
                  totals, 'report_profit_item_sub')


@router.route('report_profit_invoice', '/reports/profit-invoice', ('GET',))
def report_profit_invoice(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    rows = q(req.db, '''SELECT v.number, v.idate, p.name party_name, v.net,
                               (SELECT COALESCE(SUM(ii.qty*ii.cost),0) FROM invoice_items ii
                                WHERE ii.invoice_id=v.id) cost
                        FROM invoices v LEFT JOIN customers p ON p.id=v.party_id
                        WHERE v.itype='sale' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?
                        ORDER BY v.id DESC''', (d1, d2))
    for row in rows:
        row['profit'] = round(core.safe_float(row['net']) - core.safe_float(row['cost']), 2)
    totals = {'party_name': T(req, 'total'), 'net': sum(core.safe_float(r['net']) for r in rows),
              'cost': sum(core.safe_float(r['cost']) for r in rows),
              'profit': sum(core.safe_float(r['profit']) for r in rows)}
    columns = [{'key': 'number', 'label': 'invoice_no'}, {'key': 'idate', 'label': 'date'},
               {'key': 'party_name', 'label': 'customer'},
               {'key': 'net', 'label': 'net', 'kind': 'money', 'align': 'end'},
               {'key': 'cost', 'label': 'cost', 'kind': 'money', 'align': 'end'},
               {'key': 'profit', 'label': 'profit', 'kind': 'money', 'align': 'end'}]
    return report(req, 'report.html', 'report_profit_invoice', 'profit-invoices.xlsx', columns, rows,
                  totals)


# --------------------------------------------------------------------------- #
# profit & loss
# --------------------------------------------------------------------------- #
@router.route('report_pl', '/reports/pl', ('GET',))
def report_pl(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    revenue = scalar(req.db, "SELECT COALESCE(SUM(net-tax),0) v FROM invoices "
                             "WHERE itype='sale' AND status!='cancelled' AND idate BETWEEN ? AND ?",
                     (d1, d2))
    cogs = scalar(req.db, '''SELECT COALESCE(SUM(ii.qty*ii.cost),0) v FROM invoice_items ii
                             JOIN invoices v ON v.id=ii.invoice_id
                             WHERE v.itype='sale' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?''',
                  (d1, d2))
    returns = scalar(req.db, "SELECT COALESCE(SUM(amount),0) v FROM credit_notes "
                             "WHERE party_type='customer' AND ctype='discount' AND cdate BETWEEN ? AND ?",
                     (d1, d2))
    withdrawals = scalar(req.db, 'SELECT COALESCE(SUM(amount),0) v FROM withdrawals '
                                 'WHERE wdate BETWEEN ? AND ?', (d1, d2))
    damages = scalar(req.db, '''SELECT COALESCE(SUM(d.qty * COALESCE((SELECT factor FROM item_units u
                               WHERE u.id=d.unit_id),1) * i.purchase_price),0) v
                               FROM damages d JOIN items i ON i.id=d.item_id
                               WHERE d.ddate BETWEEN ? AND ?''', (d1, d2))
    journal_expense = scalar(req.db, '''SELECT COALESCE(SUM(l.debit-l.credit),0) v FROM journal_lines l
                                        JOIN journals j ON j.id=l.journal_id
                                        JOIN accounts_chart a ON a.id=l.account_id
                                        WHERE a.atype='expense' AND j.jdate BETWEEN ? AND ?''', (d1, d2))
    journal_revenue = scalar(req.db, '''SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l
                                        JOIN journals j ON j.id=l.journal_id
                                        JOIN accounts_chart a ON a.id=l.account_id
                                        WHERE a.atype='revenue' AND j.jdate BETWEEN ? AND ?''', (d1, d2))
    gross = revenue - returns - cogs
    expenses = withdrawals + damages + journal_expense
    net = gross - expenses
    sections = [
        {'title': 'revenue', 'rows': [
            {'label': 'sales', 'value': revenue},
            {'label': 'credit_notes', 'value': -returns},
            {'label': 'journal_revenue', 'value': journal_revenue},
        ], 'total': revenue - returns + journal_revenue},
        {'title': 'cogs', 'rows': [{'label': 'cost_of_sales', 'value': -cogs}], 'total': -cogs},
        {'title': 'expenses', 'rows': [
            {'label': 'expense_withdrawals', 'value': -withdrawals},
            {'label': 'expense_damages', 'value': -damages},
            {'label': 'expense_journals', 'value': -journal_expense},
        ], 'total': -expenses},
    ]
    if req.q('export'):
        data = [[T(req, 'description'), T(req, 'amount')]]
        for section in sections:
            data.append([T(req, section['title']), ''])
            for row in section['rows']:
                data.append(['  ' + T(req, row['label']), round(row['value'], 2)])
            data.append([T(req, 'total') + ' ' + T(req, section['title']), round(section['total'], 2)])
        data.append([T(req, 'net_profit'), round(net, 2)])
        return export_rows('profit-loss.xlsx', data)
    return render(req, 'pl.html', title=T(req, 'report_pl'), subtitle=T(req, 'report_pl_sub'),
                  active='reports_pl', sections=sections, gross=gross, net=net,
                  d1=d1, d2=d2, show_dates=True, revenue=revenue)


# --------------------------------------------------------------------------- #
# stock reports
# --------------------------------------------------------------------------- #
@router.route('report_stock', '/reports/stock', ('GET',))
def report_stock(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    rows = q(req.db, '''SELECT i.code, i.name, s.name store_name,
                               COALESCE(SUM(m.qty),0) qty, i.purchase_price,
                               COALESCE(SUM(m.qty),0)*i.purchase_price value
                        FROM items i JOIN stores s
                        LEFT JOIN stock_movements m ON m.item_id=i.id AND m.store_id=s.id
                        WHERE i.active=1 GROUP BY i.id, i.code, i.name, s.id, s.name, i.purchase_price
                        HAVING COALESCE(SUM(m.qty),0) != 0 ORDER BY i.name''')
    totals = {'name': T(req, 'total'), 'qty': sum(core.safe_float(r['qty']) for r in rows),
              'value': sum(core.safe_float(r['value']) for r in rows)}
    columns = [{'key': 'name', 'label': 'item'}, {'key': 'code', 'label': 'code'},
               {'key': 'store_name', 'label': 'store'},
               {'key': 'qty', 'label': 'qty', 'kind': 'int', 'align': 'end'},
               {'key': 'purchase_price', 'label': 'cost', 'kind': 'money', 'align': 'end'},
               {'key': 'value', 'label': 'value', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_stock', 'stock-report.xlsx', columns, rows, totals,
                          show_dates=False)


@router.route('report_expiry', '/reports/expiry', ('GET',))
def report_expiry(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    window = core.db.add_period(today(), 'day', int(req.company.get('expiry_alert_days') or 30))
    rows = q(req.db, '''SELECT i.name, i.code, s.name store_name, b.qty, b.expiry_date
                        FROM item_batches b JOIN items i ON i.id=b.item_id
                        JOIN stores s ON s.id=b.store_id
                        WHERE b.qty>0 AND b.expiry_date!='' AND b.expiry_date<=?
                        ORDER BY b.expiry_date''', (window,))
    base = datetime.date.fromisoformat(today())
    for row in rows:
        try:
            row['days'] = (datetime.date.fromisoformat(row['expiry_date']) - base).days
        except (TypeError, ValueError):
            row['days'] = 0
        row['state'] = T(req, 'expired') if (row['days'] or 0) < 0 else T(req, 'expiring_soon')
    totals = {'name': T(req, 'total'), 'qty': sum(core.safe_float(r['qty']) for r in rows)}
    columns = [{'key': 'name', 'label': 'item'}, {'key': 'code', 'label': 'code'},
               {'key': 'store_name', 'label': 'store'},
               {'key': 'qty', 'label': 'qty', 'kind': 'int', 'align': 'end'},
               {'key': 'expiry_date', 'label': 'expiry'},
               {'key': 'days', 'label': 'days_left', 'kind': 'int', 'align': 'end'},
               {'key': 'state', 'label': 'status'}]
    return generic_report(req, 'report_expiry', 'expiry-report.xlsx', columns, rows, totals,
                          show_dates=False)


@router.route('report_reorder', '/reports/reorder', ('GET',))
def report_reorder(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    rows = q(req.db, '''SELECT * FROM (
                          SELECT i.code, i.name, i.reorder_level, i.purchase_price,
                                 COALESCE((SELECT SUM(m.qty) FROM stock_movements m
                                           WHERE m.item_id=i.id),0) stock
                          FROM items i WHERE i.active=1 AND i.reorder_level>0) t
                        WHERE stock <= reorder_level ORDER BY reorder_level - stock DESC''')
    for row in rows:
        row['shortage'] = max(core.safe_float(row['reorder_level']) - core.safe_float(row['stock']), 0)
        row['cost_to_order'] = round(core.safe_float(row['shortage']) * core.safe_float(row['purchase_price']), 2)
    totals = {'name': T(req, 'total'),
              'cost_to_order': sum(core.safe_float(r['cost_to_order']) for r in rows)}
    columns = [{'key': 'name', 'label': 'item'}, {'key': 'code', 'label': 'code'},
               {'key': 'stock', 'label': 'stock', 'kind': 'int', 'align': 'end'},
               {'key': 'reorder_level', 'label': 'reorder_level', 'kind': 'int', 'align': 'end'},
               {'key': 'shortage', 'label': 'shortage', 'kind': 'int', 'align': 'end'},
               {'key': 'cost_to_order', 'label': 'cost', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_reorder', 'reorder-report.xlsx', columns, rows, totals,
                          show_dates=False)


@router.route('report_sellers', '/reports/sellers', ('GET',))
def report_sellers(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req, 365)
    rows = q(req.db, '''SELECT i.name, i.code, SUM(ii.qty) qty,
                               SUM(ii.qty * COALESCE((SELECT factor FROM item_units u WHERE u.id=ii.unit_id),1)) base_qty,
                               SUM(ii.total) revenue
                        FROM invoice_items ii JOIN invoices v ON v.id=ii.invoice_id
                        JOIN items i ON i.id=ii.item_id
                        WHERE v.itype='sale' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?
                        GROUP BY i.id, i.name, i.code ORDER BY revenue DESC''', (d1, d2))
    best, worst = rows[:10], rows[-10:][::-1] if rows else []
    if req.q('export'):
        data = [[T(req, 'best_sellers')], [T(req, 'item'), T(req, 'qty'), T(req, 'revenue')]]
        data += [[r['name'], r['qty'], r['revenue']] for r in best]
        data += [[], [T(req, 'worst_sellers')], [T(req, 'item'), T(req, 'qty'), T(req, 'revenue')]]
        data += [[r['name'], r['qty'], r['revenue']] for r in worst]
        return export_rows('best-worst-sellers.xlsx', data)
    return render(req, 'report_sellers.html', title=T(req, 'report_sellers'), active='reports_sellers',
                  best=best, worst=worst, d1=d1, d2=d2, show_dates=True)


# --------------------------------------------------------------------------- #
# statements / cash / aging / tax
# --------------------------------------------------------------------------- #
def _statement_rows(req, party_type, party_id, d1, d2):
    table = 'customers' if party_type == 'customer' else 'suppliers'
    party = one(req.db, f'SELECT * FROM {table} WHERE id=?', (party_id,))
    if not party:
        return None, [], {}
    opening = core.safe_float(party.get('opening_balance'))
    events = []
    for row in q(req.db, '''SELECT idate d, number n, net, paid, itype FROM invoices
                            WHERE party_type=? AND party_id=? AND status!='cancelled'
                            AND idate>=? AND idate<=?''', (party_type, party_id, d1, d2)):
        amount = core.safe_float(row['net'])
        paid_amount = core.safe_float(row['paid'])
        events.append((row['d'], T(req, 'invoice') + ' ' + row['n'], amount, 0.0))
        if paid_amount:
            events.append((row['d'], T(req, 'paid') + ' ' + row['n'], 0.0, paid_amount))
    for row in q(req.db, '''SELECT vdate d, number n, vtype, amount FROM vouchers
                            WHERE party_type=? AND party_id=? AND vdate>=? AND vdate<=?''',
                 (party_type, party_id, d1, d2)):
        label = T(req, 'receipt' if row['vtype'] == 'receipt' else 'payment') + ' ' + row['n']
        amount = core.safe_float(row['amount'])
        if row['vtype'] == 'receipt':
            events.append((row['d'], label, 0.0, amount))
        else:
            events.append((row['d'], label, amount, 0.0))
    for row in q(req.db, '''SELECT cdate d, ctype, amount, note FROM credit_notes
                            WHERE party_type=? AND party_id=? AND cdate>=? AND cdate<=?''',
                 (party_type, party_id, d1, d2)):
        is_add = row['ctype'] == 'add'
        amount = core.safe_float(row['amount'])
        debit, credit = (amount, 0.0) if is_add else (0.0, amount)
        events.append((row['d'], T(req, 'note_add' if is_add else 'note_discount'), debit, credit))
    balance = opening
    rows = [{'date': '', 'doc': T(req, 'opening_balance'), 'debit': '', 'credit': '',
             'balance': round(opening, 2)}]
    for date, doc, debit, credit in sorted(events, key=lambda e: e[0] or ''):
        balance = round(balance + debit - credit, 2)
        rows.append({'date': date, 'doc': doc, 'debit': round(debit, 2) if debit else '',
                     'credit': round(credit, 2) if credit else '', 'balance': balance})
    return party, rows, {'opening': opening, 'closing': balance,
                         'debit': sum(e[2] for e in events), 'credit': sum(e[3] for e in events)}


@router.route('report_statement', '/reports/statement', ('GET',))
def report_statement(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    party_type = 'supplier' if req.q('ptype') == 'supplier' else 'customer'
    table = 'customers' if party_type == 'customer' else 'suppliers'
    parties = q(req.db, f'SELECT id, name, balance FROM {table} WHERE active=1 ORDER BY name')
    party_id = req.i('pid') or (parties[0]['id'] if parties else 0)
    d1, d2 = date_range(req, 3650)
    party, rows, totals = _statement_rows(req, party_type, party_id, d1, d2)
    if req.q('export') and party:
        data = [[T(req, 'statement_for', name=party['name'])],
                [T(req, 'date'), T(req, 'description'), T(req, 'debit'), T(req, 'credit'), T(req, 'balance')]]
        data += [[r['date'], r['doc'], r['debit'], r['credit'], r['balance']] for r in rows]
        return export_rows('statement.xlsx', data)
    return render(req, 'statement.html', title=T(req, 'report_statement'), active='reports_statement',
                  parties=parties, party=party, party_type=party_type, rows=rows, totals=totals,
                  d1=d1, d2=d2)


@router.route('report_cashflow', '/reports/cashflow', ('GET',))
def report_cashflow(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    movements = []
    for row in q(req.db, '''SELECT v.vdate d, v.number, v.vtype, t.name treasury_name, v.amount,
                                   v.note, COALESCE(c.name, s.name) party_name
                            FROM vouchers v JOIN treasuries t ON t.id=v.treasury_id
                            LEFT JOIN customers c ON c.id=v.party_id AND v.party_type='customer'
                            LEFT JOIN suppliers s ON s.id=v.party_id AND v.party_type='supplier'
                            WHERE v.vdate BETWEEN ? AND ?''', (d1, d2)):
        incoming = row['vtype'] == 'receipt'
        movements.append({'date': row['d'], 'doc': row['number'],
                           'kind': T(req, 'receipt' if incoming else 'payment'),
                           'treasury': row['treasury_name'], 'party': row['party_name'] or '',
                           'in': core.safe_float(row['amount']) if incoming else 0,
                           'out': 0 if incoming else core.safe_float(row['amount']),
                           'note': row['note'] or ''})
    for row in q(req.db, '''SELECT t.tdate d, t.number, t.amount, s1.name f, s2.name t2
                            FROM treasury_transfers t JOIN treasuries s1 ON s1.id=t.tfrom
                            JOIN treasuries s2 ON s2.id=t.tto WHERE t.tdate BETWEEN ? AND ?''', (d1, d2)):
        movements.append({'date': row['d'], 'doc': row['number'] or '-',
                          'kind': T(req, 'treasury_transfers'), 'treasury': f"{row['f']} → {row['t2']}",
                          'party': '', 'in': 0, 'out': 0, 'note': ''})
    for row in q(req.db, 'SELECT wdate d, amount, wtype, note FROM withdrawals WHERE wdate BETWEEN ? AND ?',
                 (d1, d2)):
        movements.append({'date': row['d'], 'doc': '-', 'kind': T(req, 'withdrawals'),
                          'treasury': '', 'party': '',
                          'in': 0, 'out': core.safe_float(row['amount']),
                          'note': T(req, 'withdrawal_personal' if row['wtype'] == 'personal'
                                    else 'withdrawal_gift')})
    movements.sort(key=lambda m: (m['date'] or '', m['doc'] or ''))
    totals = {'kind': T(req, 'total'), 'in': sum(m['in'] for m in movements),
              'out': sum(m['out'] for m in movements)}
    columns = [{'key': 'date', 'label': 'date'}, {'key': 'doc', 'label': 'number'},
               {'key': 'kind', 'label': 'type'}, {'key': 'treasury', 'label': 'treasury'},
               {'key': 'party', 'label': 'party'},
               {'key': 'in', 'label': 'movement_in', 'kind': 'money', 'align': 'end'},
               {'key': 'out', 'label': 'movement_out', 'kind': 'money', 'align': 'end'},
               {'key': 'note', 'label': 'description'}]
    return generic_report(req, 'report_cashflow', 'cash-movement.xlsx', columns, movements, totals)


@router.route('report_treasury', '/reports/treasury', ('GET',))
def report_treasury(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    rows = q(req.db, '''SELECT t.name, t.ttype, t.balance, t.bank_name, t.account_no,
                               (SELECT COALESCE(SUM(v.amount),0) FROM vouchers v
                                WHERE v.treasury_id=t.id AND v.vtype='receipt') receipts,
                               (SELECT COALESCE(SUM(v.amount),0) FROM vouchers v
                                WHERE v.treasury_id=t.id AND v.vtype='payment') payments
                        FROM treasuries t ORDER BY t.ttype, t.name''')
    totals = {'name': T(req, 'total'), 'balance': sum(core.safe_float(r['balance']) for r in rows),
              'receipts': sum(core.safe_float(r['receipts']) for r in rows),
              'payments': sum(core.safe_float(r['payments']) for r in rows)}
    columns = [{'key': 'name', 'label': 'name'},
               {'key': 'ttype', 'label': 'type'},
               {'key': 'bank_name', 'label': 'bank_name'}, {'key': 'account_no', 'label': 'account_no'},
               {'key': 'receipts', 'label': 'receipt', 'kind': 'money', 'align': 'end'},
               {'key': 'payments', 'label': 'payment', 'kind': 'money', 'align': 'end'},
               {'key': 'balance', 'label': 'balance', 'kind': 'money', 'align': 'end'}]
    for row in rows:
        row['ttype'] = T(req, 'treasury_cash' if row['ttype'] == 'cash' else 'treasury_bank')
    return generic_report(req, 'report_treasury', 'treasuries.xlsx', columns, rows, totals,
                          show_dates=False)


@router.route('report_aging', '/reports/aging', ('GET',))
def report_aging(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    kind = 'supplier' if req.q('kind') == 'supplier' else 'customer'
    itype = 'purchase' if kind == 'supplier' else 'sale'
    table = 'suppliers' if kind == 'supplier' else 'customers'
    rows = q(req.db, f'''SELECT p.id, p.name, p.phone, p.balance,
                                v.number, v.idate, v.due_date, v.net, v.paid
                         FROM {table} p LEFT JOIN invoices v ON v.party_id=p.id
                         AND v.party_type=? AND v.itype=? AND v.status IN ('credit','partial')
                         ORDER BY p.name, v.due_date''', (kind, itype))
    grouped = {}
    for row in rows:
        entry = grouped.setdefault(row['id'], {'name': row['name'], 'phone': row['phone'],
                                               'balance': row['balance'],
                                               'current': 0, 'b1': 0, 'b2': 0, 'b3': 0, 'b4': 0,
                                               'total': 0})
        remaining = core.safe_float(row['net']) - core.safe_float(row['paid'])
        if not row['number'] or remaining <= 0.005:
            continue
        due = row['due_date'] or row['idate'] or today()
        try:
            days = (datetime.date.fromisoformat(today()) - datetime.date.fromisoformat(due)).days
        except ValueError:
            days = 0
        bucket = 'current' if days <= 0 else ('b1' if days <= 30 else ('b2' if days <= 60 else
                                                                      ('b3' if days <= 90 else 'b4')))
        entry[bucket] = round(entry[bucket] + remaining, 2)
        entry['total'] = round(entry['total'] + remaining, 2)
    data = list(grouped.values())
    totals = {'name': T(req, 'total'),
              'current': sum(r['current'] for r in data), 'b1': sum(r['b1'] for r in data),
              'b2': sum(r['b2'] for r in data), 'b3': sum(r['b3'] for r in data),
              'b4': sum(r['b4'] for r in data), 'total': sum(r['total'] for r in data)}
    columns = [{'key': 'name', 'label': 'party'}, {'key': 'phone', 'label': 'phone'},
               {'key': 'current', 'label': 'aging_current', 'kind': 'money', 'align': 'end'},
               {'key': 'b1', 'label': 'aging_30', 'kind': 'money', 'align': 'end'},
               {'key': 'b2', 'label': 'aging_60', 'kind': 'money', 'align': 'end'},
               {'key': 'b3', 'label': 'aging_90', 'kind': 'money', 'align': 'end'},
               {'key': 'b4', 'label': 'aging_over', 'kind': 'money', 'align': 'end'},
               {'key': 'total', 'label': 'total', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_aging', f'aging-{kind}.xlsx', columns, data, totals,
                          show_dates=False)


@router.route('report_tax', '/reports/tax', ('GET',))
def report_tax(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    sales = q(req.db, '''SELECT number, idate, party_id, net, tax, tax_percent FROM invoices
                         WHERE itype='sale' AND is_tax=1 AND status!='cancelled'
                         AND idate BETWEEN ? AND ? ORDER BY idate''', (d1, d2))
    purchases = q(req.db, '''SELECT number, idate, party_id, net, tax FROM invoices
                             WHERE itype='purchase' AND is_tax=1 AND status!='cancelled'
                             AND idate BETWEEN ? AND ? ORDER BY idate''', (d1, d2))
    output_tax = sum(core.safe_float(r['tax']) for r in sales)
    input_tax = sum(core.safe_float(r['tax']) for r in purchases)
    rows = []
    for row in sales:
        rows.append({'number': row['number'], 'date': row['idate'], 'kind': T(req, 'sales_invoices'),
                     'party': core.party_name(req.db, 'customer', row['party_id']),
                     'net': row['net'], 'tax': row['tax']})
    for row in purchases:
        rows.append({'number': row['number'], 'date': row['idate'], 'kind': T(req, 'purchase_invoices'),
                     'party': core.party_name(req.db, 'supplier', row['party_id']),
                     'net': row['net'], 'tax': row['tax']})
    totals = {'number': T(req, 'total'), 'net': sum(core.safe_float(r['net']) for r in rows),
              'tax': output_tax - input_tax}
    columns = [{'key': 'number', 'label': 'number'}, {'key': 'date', 'label': 'date'},
               {'key': 'kind', 'label': 'type'}, {'key': 'party', 'label': 'party'},
               {'key': 'net', 'label': 'net', 'kind': 'money', 'align': 'end'},
               {'key': 'tax', 'label': 'tax', 'kind': 'money', 'align': 'end'}]
    summary = {'output': output_tax, 'input': input_tax, 'due': output_tax - input_tax}
    if req.q('export'):
        data = [[T(req, 'report_tax')], [T(req, 'taxable_sales'), '', '', '', '', output_tax],
                [T(req, 'tax_paid'), '', '', '', '', input_tax], [T(req, 'tax_due'), '', '', '', '', summary['due']],
                [], [c['label'] for c in columns]]
        data += [[r['number'], r['date'], r['kind'], r['party'], r['net'], r['tax']] for r in rows]
        return export_rows('tax-report.xlsx', [[T(req, c['label']) if isinstance(c, dict) else c
                                                for c in row] for row in data])
    return render(req, 'tax.html', title=T(req, 'report_tax'), active='reports_tax', rows=rows,
                  columns=columns, totals=totals, summary=summary, d1=d1, d2=d2, show_dates=True)


@router.route('report_item_card', '/reports/item-card', ('GET',))
def report_item_card(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    items_list = q(req.db, 'SELECT id, name, code FROM items ORDER BY name')
    item_id = req.i('item') or (items_list[0]['id'] if items_list else 0)
    item = one(req.db, 'SELECT * FROM items WHERE id=?', (item_id,)) if item_id else None
    movements = q(req.db, '''SELECT m.*, s.name store_name, u.name unit_name
                             FROM stock_movements m LEFT JOIN stores s ON s.id=m.store_id
                             LEFT JOIN item_units u ON u.id=m.unit_id
                             WHERE m.item_id=? ORDER BY m.mdate, m.id''', (item_id,))
    balance = 0.0
    for row in movements:
        balance = round(balance + core.safe_float(row['qty']), 2)
        row['balance'] = balance
    stores = q(req.db, '''SELECT s.name, COALESCE(SUM(m.qty),0) qty FROM stores s
                          LEFT JOIN stock_movements m ON m.store_id=s.id AND m.item_id=?
                          GROUP BY s.id, s.name HAVING COALESCE(SUM(m.qty),0)!=0''', (item_id,))
    if req.q('export'):
        data = [[T(req, 'report_item_card')], [T(req, 'date'), T(req, 'store'), T(req, 'type'),
                                               T(req, 'qty'), T(req, 'balance')]]
        data += [[r['mdate'], r['store_name'], r['ref_type'], r['qty'], r['balance']] for r in movements]
        return export_rows('item-card.xlsx', data)
    return render(req, 'item_card.html', title=T(req, 'report_item_card'), active='reports_item_card',
                  items_list=items_list, item=item, movements=movements, stores=stores,
                  balance=balance, item_id=item_id)


@router.route('report_purchases', '/reports/purchases', ('GET',))
def report_purchases(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    rows = q(req.db, '''SELECT p.name, COUNT(v.id) invoices, COALESCE(SUM(v.net),0) net,
                               COALESCE(SUM(v.paid),0) paid, COALESCE(SUM(v.net-v.paid),0) remaining
                        FROM suppliers p LEFT JOIN invoices v ON v.party_id=p.id AND v.party_type='supplier'
                        AND v.itype='purchase' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?
                        GROUP BY p.id, p.name HAVING COUNT(v.id)>0 ORDER BY net DESC''', (d1, d2))
    totals = {'name': T(req, 'total'), 'invoices': sum(r['invoices'] for r in rows),
              'net': sum(core.safe_float(r['net']) for r in rows),
              'paid': sum(core.safe_float(r['paid']) for r in rows),
              'remaining': sum(core.safe_float(r['remaining']) for r in rows)}
    columns = [{'key': 'name', 'label': 'supplier'},
               {'key': 'invoices', 'label': 'count', 'kind': 'int', 'align': 'end'},
               {'key': 'net', 'label': 'net', 'kind': 'money', 'align': 'end'},
               {'key': 'paid', 'label': 'paid', 'kind': 'money', 'align': 'end'},
               {'key': 'remaining', 'label': 'remaining', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_purchases', 'purchases-by-supplier.xlsx', columns, rows, totals)


@router.route('report_sales_customer', '/reports/sales-customers', ('GET',))
def report_sales_customer(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    rows = q(req.db, '''SELECT p.name, COUNT(v.id) invoices, COALESCE(SUM(v.net),0) net,
                               COALESCE(SUM(v.paid),0) paid, COALESCE(SUM(v.net-v.paid),0) remaining
                        FROM customers p LEFT JOIN invoices v ON v.party_id=p.id AND v.party_type='customer'
                        AND v.itype='sale' AND v.status!='cancelled' AND v.idate BETWEEN ? AND ?
                        GROUP BY p.id, p.name HAVING COUNT(v.id)>0 ORDER BY net DESC''', (d1, d2))
    totals = {'name': T(req, 'total'), 'invoices': sum(r['invoices'] for r in rows),
              'net': sum(core.safe_float(r['net']) for r in rows),
              'paid': sum(core.safe_float(r['paid']) for r in rows),
              'remaining': sum(core.safe_float(r['remaining']) for r in rows)}
    columns = [{'key': 'name', 'label': 'customer'},
               {'key': 'invoices', 'label': 'count', 'kind': 'int', 'align': 'end'},
               {'key': 'net', 'label': 'net', 'kind': 'money', 'align': 'end'},
               {'key': 'paid', 'label': 'paid', 'kind': 'money', 'align': 'end'},
               {'key': 'remaining', 'label': 'remaining', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_sales_customer', 'sales-by-customer.xlsx', columns, rows, totals)


@router.route('report_transfers', '/reports/transfers', ('GET',))
def report_transfers(req):
    blocked = guard(req, 'p_reports')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    rows = q(req.db, '''SELECT t.number, t.tdate, s1.name from_name, s2.name to_name, i.name item_name,
                               ti.qty, u.name unit_name, i.purchase_price, ti.qty * i.purchase_price value
                        FROM transfer_items ti JOIN transfers t ON t.id=ti.transfer_id
                        JOIN items i ON i.id=ti.item_id LEFT JOIN item_units u ON u.id=ti.unit_id
                        JOIN stores s1 ON s1.id=t.from_store JOIN stores s2 ON s2.id=t.to_store
                        WHERE t.tdate BETWEEN ? AND ? ORDER BY t.tdate, t.id''', (d1, d2))
    totals = {'number': T(req, 'total'), 'qty': sum(core.safe_float(r['qty']) for r in rows),
              'value': sum(core.safe_float(r['value']) for r in rows)}
    columns = [{'key': 'number', 'label': 'transfer_no'}, {'key': 'tdate', 'label': 'date'},
               {'key': 'from_name', 'label': 'from_store'}, {'key': 'to_name', 'label': 'to_store'},
               {'key': 'item_name', 'label': 'item'}, {'key': 'unit_name', 'label': 'unit'},
               {'key': 'qty', 'label': 'qty', 'kind': 'int', 'align': 'end'},
               {'key': 'value', 'label': 'value', 'kind': 'money', 'align': 'end'}]
    return generic_report(req, 'report_transfers', 'transfers.xlsx', columns, rows, totals)


# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
def account_balances(conn, d1='', d2=''):
    where, args = '', []
    if d1 and d2:
        where = 'AND j.jdate BETWEEN ? AND ?'
        args = [d1, d2]
    return q(conn, f'''SELECT a.*, COALESCE(SUM(l.debit),0) debit, COALESCE(SUM(l.credit),0) credit,
                              COALESCE(SUM(l.debit-l.credit),0) balance
                       FROM accounts_chart a LEFT JOIN journal_lines l ON l.account_id=a.id
                       LEFT JOIN journals j ON j.id=l.journal_id {where}
                       GROUP BY a.id ORDER BY a.code''', args)


@router.route('journals', '/journals', ('GET',))
def journals(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    page, per, offset = core.page_params(req, 50)
    total = scalar(req.db, 'SELECT COUNT(*) c FROM journals')
    rows = q(req.db, '''SELECT j.*, u.full_name username,
                               (SELECT COUNT(*) FROM journal_lines l WHERE l.journal_id=j.id) lines,
                               (SELECT COALESCE(SUM(l.debit),0) FROM journal_lines l
                                WHERE l.journal_id=j.id) total
                        FROM journals j LEFT JOIN users u ON u.id=j.user_id
                        ORDER BY j.id DESC LIMIT ? OFFSET ?''', (per, offset))
    return render(req, 'journal_list.html', title=T(req, 'journal_entries'), active='journals',
                  rows=rows, pager=core.pager(req, total, page, per))


def _journal_accounts(req):
    return q(req.db, 'SELECT id, code, name, atype FROM accounts_chart WHERE is_group=0 ORDER BY code')


@router.route('journal_new', '/journals/new', ('GET', 'POST'))
def journal_new(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    if req.method == 'POST':
        account_ids = req.flist('account_id')
        debits = req.flist('debit')
        credits = req.flist('credit')
        notes = req.flist('line_note')
        lines = []
        for index, account_id in enumerate(account_ids):
            if not str(account_id or '').isdigit():
                continue
            debit = core.safe_float(debits[index] if index < len(debits) else 0)
            credit = core.safe_float(credits[index] if index < len(credits) else 0)
            if debit == 0 and credit == 0:
                continue
            lines.append((int(account_id), debit, credit,
                          (notes[index] if index < len(notes) else '') or ''))
        total_debit = sum(line[1] for line in lines)
        total_credit = sum(line[2] for line in lines)
        if not lines or abs(total_debit - total_credit) > 0.01:
            flash(req, T(req, 'journal_unbalanced'), 'danger')
            return redirect(url_for('journal_new'), req)
        count = scalar(req.db, 'SELECT COUNT(*) c FROM journals') + 1
        number = f'JV-{count:05d}'
        journal_id = x(req.db, 'INSERT INTO journals(number,jdate,note,user_id,ctime,ref) '
                               'VALUES(?,?,?,?,?,?)',
                       (number, req.f('jdate') or today(), (req.f('note') or '').strip(),
                        req.user['id'], datetime.datetime.now().timestamp(),
                        (req.f('ref') or '').strip()))
        for account_id, debit, credit, line_note in lines:
            x(req.db, 'INSERT INTO journal_lines(journal_id,account_id,debit,credit,note) '
                      'VALUES(?,?,?,?,?)', (journal_id, account_id, debit, credit, line_note))
        audit(req, 'create', 'journals', journal_id, number)
        flash(req, T(req, 'journal_saved'), 'success')
        return redirect(url_for('journal_view', jid=journal_id), req)
    return render(req, 'journal_form.html', title=T(req, 'new_journal'), active='journals',
                  accounts=_journal_accounts(req))


@router.route('journal_view', '/journals/<jid>', ('GET',))
def journal_view(req, jid):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    journal = one(req.db, 'SELECT * FROM journals WHERE id=?', (jid,))
    if not journal:
        return core.not_found()
    rows = q(req.db, '''SELECT l.*, a.name account_name, a.code account_code FROM journal_lines l
                        JOIN accounts_chart a ON a.id=l.account_id WHERE l.journal_id=?''', (jid,))
    return render(req, 'journal_view.html', title=T(req, 'journal_entries'), active='journals',
                  journal=journal, rows=rows,
                  total_debit=sum(core.safe_float(r['debit']) for r in rows),
                  total_credit=sum(core.safe_float(r['credit']) for r in rows))


@router.route('journal_delete', '/journals/<jid>/delete', ('POST',))
def journal_delete(req, jid):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    ex(req.db, 'DELETE FROM journal_lines WHERE journal_id=?', (jid,))
    ex(req.db, 'DELETE FROM journals WHERE id=?', (jid,))
    audit(req, 'delete', 'journals', jid)
    flash(req, T(req, 'deleted'), 'warning')
    return redirect(url_for('journals'), req)


@router.route('trial_balance', '/trial-balance', ('GET',))
def trial_balance(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    d1, d2 = date_range(req, 365)
    rows = account_balances(req.db, d1, d2)
    total_debit = sum(core.safe_float(r['debit']) for r in rows)
    total_credit = sum(core.safe_float(r['credit']) for r in rows)
    if req.q('export'):
        data = [[T(req, 'account_code'), T(req, 'account_name'), T(req, 'debit'), T(req, 'credit'),
                 T(req, 'balance')]]
        data += [[r['code'], r['name'], r['debit'], r['credit'], r['balance']] for r in rows]
        data.append([T(req, 'total'), '', total_debit, total_credit, round(total_debit - total_credit, 2)])
        return export_rows('trial-balance.xlsx', data)
    return render(req, 'trial_balance.html', title=T(req, 'trial_balance'), active='trial_balance',
                  rows=rows, total_debit=total_debit, total_credit=total_credit, d1=d1, d2=d2)


def financial_snapshot(conn):
    cash = scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM treasuries')
    inventory = scalar(conn, '''SELECT COALESCE(SUM(m.qty * i.purchase_price),0) v
                                FROM stock_movements m JOIN items i ON i.id=m.item_id''')
    receivables = scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM customers WHERE balance>0')
    customer_credits = scalar(conn, 'SELECT COALESCE(SUM(-balance),0) v FROM customers WHERE balance<0')
    payables = scalar(conn, 'SELECT COALESCE(SUM(balance),0) v FROM suppliers WHERE balance>0')
    supplier_advances = scalar(conn, 'SELECT COALESCE(SUM(-balance),0) v FROM suppliers WHERE balance<0')
    output_tax = scalar(conn, "SELECT COALESCE(SUM(tax),0) v FROM invoices WHERE itype='sale' "
                              "AND status!='cancelled'")
    input_tax = scalar(conn, "SELECT COALESCE(SUM(tax),0) v FROM invoices WHERE itype='purchase' "
                             "AND status!='cancelled'")
    return {'cash': cash, 'inventory': inventory, 'receivables': receivables,
            'customer_credits': customer_credits, 'payables': payables,
            'supplier_advances': supplier_advances, 'tax': output_tax - input_tax,
            'output_tax': output_tax, 'input_tax': input_tax}


@router.route('balance_sheet', '/balance-sheet', ('GET',))
def balance_sheet(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    snapshot = financial_snapshot(req.db)
    rows = account_balances(req.db)
    groups = {'asset': [], 'liability': [], 'equity': [], 'revenue': [], 'expense': []}
    for row in rows:
        groups.setdefault(row['atype'], []).append(row)
    ledger_assets = sum(core.safe_float(r['balance']) for r in groups['asset'])
    ledger_liabilities = -sum(core.safe_float(r['balance']) for r in groups['liability'])
    ledger_equity = -sum(core.safe_float(r['balance']) for r in groups['equity'])
    ledger_profit = -sum(core.safe_float(r['balance']) for r in groups['revenue']) - \
        sum(core.safe_float(r['balance']) for r in groups['expense'])
    assets = snapshot['cash'] + snapshot['inventory'] + snapshot['receivables'] + ledger_assets
    liabilities = snapshot['payables'] + max(snapshot['tax'], 0) + ledger_liabilities
    equity_ledger = ledger_equity + ledger_profit
    retained = round(assets - liabilities - equity_ledger, 2)
    if req.q('export'):
        data = [[T(req, 'balance_sheet')], [T(req, 'description'), T(req, 'amount')],
                [T(req, 'cash_balance'), snapshot['cash']], [T(req, 'inventory_value'), snapshot['inventory']],
                [T(req, 'receivables'), snapshot['receivables']],
                [T(req, 'assets') + ' ' + T(req, 'accounts'), ledger_assets],
                [T(req, 'total') + ' ' + T(req, 'assets'), round(assets, 2)],
                [T(req, 'payables'), snapshot['payables']], [T(req, 'tax_due'), snapshot['tax']],
                [T(req, 'liabilities') + ' ' + T(req, 'accounts'), ledger_liabilities],
                [T(req, 'equity') + ' ' + T(req, 'accounts'), equity_ledger],
                [T(req, 'retained_earnings'), retained],
                [T(req, 'total') + ' ' + T(req, 'liabilities') + '+' + T(req, 'equity'),
                 round(liabilities + equity_ledger + retained, 2)]]
        return export_rows('balance-sheet.xlsx', data)
    return render(req, 'balance_sheet.html', title=T(req, 'balance_sheet'), active='balance_sheet',
                  snapshot=snapshot, groups=groups, assets=assets, liabilities=liabilities,
                  equity_ledger=equity_ledger, retained=retained,
                  total_liab_equity=liabilities + equity_ledger + retained, ledger_assets=ledger_assets,
                  ledger_liabilities=ledger_liabilities, ledger_profit=ledger_profit)


@router.route('cash_flow', '/cash-flow', ('GET',))
def cash_flow(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    d1, d2 = date_range(req)
    receipts = scalar(req.db, "SELECT COALESCE(SUM(amount),0) v FROM vouchers WHERE vtype='receipt' "
                              "AND vdate BETWEEN ? AND ?", (d1, d2))
    payments = scalar(req.db, "SELECT COALESCE(SUM(amount),0) v FROM vouchers WHERE vtype='payment' "
                              "AND vdate BETWEEN ? AND ?", (d1, d2))
    invoice_sales = scalar(req.db, "SELECT COALESCE(SUM(paid),0) v FROM invoices WHERE itype='sale' "
                                   "AND status!='cancelled' AND idate BETWEEN ? AND ?", (d1, d2))
    invoice_purchases = scalar(req.db, "SELECT COALESCE(SUM(paid),0) v FROM invoices WHERE itype='purchase' "
                                       "AND status!='cancelled' AND idate BETWEEN ? AND ?", (d1, d2))
    withdrawals = scalar(req.db, 'SELECT COALESCE(SUM(amount),0) v FROM withdrawals WHERE wdate BETWEEN ? AND ?',
                         (d1, d2))
    closing = scalar(req.db, 'SELECT COALESCE(SUM(balance),0) v FROM treasuries')
    inflow = receipts + invoice_sales
    outflow = payments + invoice_purchases + withdrawals
    net = inflow - outflow
    sections = [
        {'title': 'receipts', 'rows': [
            {'label': 'cash_from_customers', 'value': invoice_sales},
            {'label': 'receipt', 'value': receipts}], 'total': inflow},
        {'title': 'payments', 'rows': [
            {'label': 'cash_to_suppliers', 'value': -invoice_purchases},
            {'label': 'payment', 'value': -payments},
            {'label': 'withdrawals', 'value': -withdrawals}], 'total': -outflow},
    ]
    if req.q('export'):
        data = [[T(req, 'cash_flow')], [T(req, 'description'), T(req, 'amount')]]
        for section in sections:
            data.append([T(req, section['title']), ''])
            data += [['  ' + T(req, row['label']), row['value']] for row in section['rows']]
            data.append([T(req, 'total'), section['total']])
        data += [[T(req, 'cash_net'), net], [T(req, 'closing_balance'), closing]]
        return export_rows('cash-flow.xlsx', data)
    return render(req, 'cashflow.html', title=T(req, 'cash_flow'), active='cash_flow', sections=sections,
                  inflow=inflow, outflow=outflow, net=net, closing=closing,
                  opening=closing - net, d1=d1, d2=d2)


@router.route('equity_statement', '/equity', ('GET',))
def equity_statement(req):
    blocked = guard(req, 'p_acc')
    if blocked:
        return blocked
    d1, d2 = date_range(req, 365)
    capital = scalar(req.db, '''SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l
                                JOIN journals j ON j.id=l.journal_id
                                JOIN accounts_chart a ON a.id=l.account_id
                                WHERE a.atype='equity' AND j.jdate BETWEEN ? AND ?''', (d1, d2))
    profit = scalar(req.db, '''SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l
                               JOIN journals j ON j.id=l.journal_id
                               JOIN accounts_chart a ON a.id=l.account_id
                               WHERE a.atype='revenue' AND j.jdate BETWEEN ? AND ?''', (d1, d2)) - \
        scalar(req.db, '''SELECT COALESCE(SUM(l.debit-l.credit),0) v FROM journal_lines l
                          JOIN journals j ON j.id=l.journal_id
                          JOIN accounts_chart a ON a.id=l.account_id
                          WHERE a.atype='expense' AND j.jdate BETWEEN ? AND ?''', (d1, d2))
    sales_profit = scalar(req.db, '''SELECT COALESCE(SUM(ii.total - ii.qty*ii.cost),0) v FROM invoice_items ii
                                     JOIN invoices v ON v.id=ii.invoice_id
                                     WHERE v.itype='sale' AND v.status!='cancelled'
                                     AND v.idate BETWEEN ? AND ?''', (d1, d2))
    drawings = scalar(req.db, 'SELECT COALESCE(SUM(amount),0) v FROM withdrawals WHERE wdate BETWEEN ? AND ?',
                      (d1, d2))
    rows = [{'label': 'capital', 'value': capital},
            {'label': 'journal_profit', 'value': profit},
            {'label': 'sales_profit', 'value': sales_profit},
            {'label': 'expense_withdrawals', 'value': -drawings}]
    total = capital + profit + sales_profit - drawings
    if req.q('export'):
        data = [[T(req, 'equity_statement')], [T(req, 'description'), T(req, 'amount')]]
        data += [[T(req, r['label']), r['value']] for r in rows]
        data.append([T(req, 'total'), total])
        return export_rows('equity.xlsx', data)
    return render(req, 'equity.html', title=T(req, 'equity_statement'), active='equity_statement',
                  rows=rows, total=total, d1=d1, d2=d2)
