# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - vouchers, cheques, installments and service payments."""
import time

import core
import db
from core import (T, add_batch, audit, base_qty, can, default_treasury, export_rows, flash, guard,
                  money, next_number, one, party_delta, q, render, scalar, scoped_treasuries,
                  today, treasury_delta, x)
from db import ex
from framework import redirect, router, url_for


# --------------------------------------------------------------------------- #
# vouchers (receipt / payment)
# --------------------------------------------------------------------------- #
def _voucher_data(req):
    return {
        'vtype': 'payment' if req.f('vtype') == 'payment' else 'receipt',
        'treasury_id': req.i('treasury_id') or default_treasury(req),
        'party_type': req.f('party_type') if req.f('party_type') in ('customer', 'supplier') else '',
        'party_id': req.i('party_id'),
        'amount': core.safe_float(req.f('amount')),
        'vdate': req.f('vdate') or today(),
        'note': (req.f('note') or '').strip(),
    }


def _voucher_effect(req, data, reverse=False):
    factor = -1 if reverse else 1
    sign = 1 if data['vtype'] == 'receipt' else -1
    treasury_delta(req.db, data['treasury_id'], factor * sign * data['amount'])
    if data['party_id'] and data['party_type']:
        party_delta(req.db, data['party_type'], data['party_id'], factor * -data['amount'])


@router.route('vouchers', '/vouchers', ('GET',))
def vouchers(req):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    where, args = [], []
    if req.q('vtype'):
        where.append('v.vtype=?')
        args.append(req.q('vtype'))
    if req.q('treasury'):
        where.append('v.treasury_id=?')
        args.append(req.q('treasury'))
    if req.q('s'):
        where.append('(v.number LIKE ? OR v.note LIKE ?)')
        args += [f"%{req.q('s')}%"] * 2
    if req.q('d1'):
        where.append('v.vdate>=?')
        args.append(req.q('d1'))
    if req.q('d2'):
        where.append('v.vdate<=?')
        args.append(req.q('d2'))
    clause = 'WHERE ' + ' AND '.join(where) if where else ''
    page, per, offset = core.page_params(req, 50)
    total = scalar(req.db, f'SELECT COUNT(*) c FROM vouchers v {clause}', args)
    rows = q(req.db, f'''SELECT v.*, t.name treasury_name,
                                COALESCE(c.name, s.name) party_name
                         FROM vouchers v JOIN treasuries t ON t.id=v.treasury_id
                         LEFT JOIN customers c ON c.id=v.party_id AND v.party_type='customer'
                         LEFT JOIN suppliers s ON s.id=v.party_id AND v.party_type='supplier'
                         {clause} ORDER BY v.id DESC LIMIT ? OFFSET ?''', args + [per, offset])
    totals = one(req.db, f'''SELECT COALESCE(SUM(CASE WHEN v.vtype='receipt' THEN v.amount ELSE 0 END),0) receipts,
                                    COALESCE(SUM(CASE WHEN v.vtype='payment' THEN v.amount ELSE 0 END),0) payments
                             FROM vouchers v {clause}''', args) or {}
    if req.q('export'):
        header = [T(req, 'number'), T(req, 'date'), T(req, 'voucher_type'), T(req, 'treasury'),
                  T(req, 'party'), T(req, 'amount'), T(req, 'description')]
        data = [header] + [[r['number'], r['vdate'],
                            T(req, 'receipt' if r['vtype'] == 'receipt' else 'payment'),
                            r['treasury_name'], r['party_name'] or '', r['amount'], r['note']]
                           for r in rows]
        return export_rows('vouchers.xlsx', data)
    return render(req, 'voucher_list.html', title=T(req, 'vouchers'), active='vouchers', rows=rows,
                  totals=totals, treasuries=q(req.db, 'SELECT id,name FROM treasuries ORDER BY name'),
                  pager=core.pager(req, total, page, per))


def _voucher_form(req, row=None):
    return render(req, 'voucher_form.html', title=T(req, 'new_voucher'), active='vouchers',
                  row=row or {'vdate': today(), 'vtype': 'receipt',
                              'treasury_id': default_treasury(req)},
                  treasuries=scoped_treasuries(req),
                  customers=q(req.db, 'SELECT id,name FROM customers ORDER BY name'),
                  suppliers=q(req.db, 'SELECT id,name FROM suppliers ORDER BY name'))


@router.route('voucher_new', '/vouchers/new', ('GET', 'POST'))
def voucher_new(req):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    if req.method == 'POST':
        data = _voucher_data(req)
        if data['amount'] <= 0:
            flash(req, T(req, 'required_field'), 'danger')
            return redirect(url_for('voucher_new'), req)
        data['user_id'] = req.user['id']
        data['ctime'] = time.time()
        data['number'] = next_number(req, 'voucher')
        columns = ', '.join(data.keys())
        voucher_id = x(req.db, f'INSERT INTO vouchers({columns}) VALUES({",".join("?" * len(data))})',
                       tuple(data.values()))
        _voucher_effect(req, data)
        audit(req, 'create', 'vouchers', voucher_id, data['number'])
        flash(req, T(req, 'saved'), 'success')
        return redirect(url_for('voucher_print', vid=voucher_id), req)
    return _voucher_form(req)


@router.route('voucher_edit', '/vouchers/<vid>/edit', ('GET', 'POST'))
def voucher_edit(req, vid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    row = one(req.db, 'SELECT * FROM vouchers WHERE id=?', (vid,))
    if not row:
        return core.not_found()
    if req.method == 'POST':
        old = dict(row)
        _voucher_effect(req, old, reverse=True)
        data = _voucher_data(req)
        if data['amount'] <= 0:
            _voucher_effect(req, old)
            flash(req, T(req, 'required_field'), 'danger')
            return redirect(url_for('voucher_edit', vid=vid), req)
        sets = ', '.join(f'{key}=?' for key in data)
        ex(req.db, f'UPDATE vouchers SET {sets} WHERE id=?', tuple(data.values()) + (vid,))
        _voucher_effect(req, data)
        audit(req, 'update', 'vouchers', vid)
        flash(req, T(req, 'saved'), 'success')
        return redirect(url_for('voucher_print', vid=vid), req)
    return _voucher_form(req, row)


@router.route('voucher_delete', '/vouchers/<vid>/delete', ('POST',))
def voucher_delete(req, vid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    row = one(req.db, 'SELECT * FROM vouchers WHERE id=?', (vid,))
    if row:
        _voucher_effect(req, row, reverse=True)
        ex(req.db, 'DELETE FROM vouchers WHERE id=?', (vid,))
        audit(req, 'delete', 'vouchers', vid)
        flash(req, T(req, 'deleted'), 'warning')
    return redirect(url_for('vouchers'), req)


@router.route('voucher_print', '/vouchers/<vid>/print', ('GET',))
def voucher_print(req, vid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    row = one(req.db, 'SELECT * FROM vouchers WHERE id=?', (vid,))
    if not row:
        return core.not_found()
    treasury = one(req.db, 'SELECT * FROM treasuries WHERE id=?', (row['treasury_id'],)) or {}
    party = {}
    if row['party_id']:
        table = 'customers' if row['party_type'] == 'customer' else 'suppliers'
        party = one(req.db, f'SELECT * FROM {table} WHERE id=?', (row['party_id'],)) or {}
    return render(req, 'voucher_print.html', title=T(req, 'vouchers'), row=row, treasury=treasury,
                  party=party, active='vouchers')


# --------------------------------------------------------------------------- #
# cheques
# --------------------------------------------------------------------------- #
@router.route('cheque_clear', '/cheques/<cid>/clear', ('POST',))
def cheque_clear(req, cid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    cheque = one(req.db, 'SELECT * FROM cheques WHERE id=?', (cid,))
    if not cheque:
        return core.not_found()
    if cheque['status'] == 'cleared':
        flash(req, T(req, 'cheque_cleared'), 'info')
        return redirect(url_for('cheques'), req)
    treasury_id = cheque['treasury_id'] or default_treasury(req)
    incoming = cheque['ctype'] == 'in'
    number = next_number(req, 'voucher')
    voucher_id = x(req.db, 'INSERT INTO vouchers(number,vtype,treasury_id,party_type,party_id,amount,'
                            'vdate,note,user_id,ctime,cheque_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (number, 'receipt' if incoming else 'payment', treasury_id, cheque['party_type'],
                    cheque['party_id'], cheque['amount'], today(),
                    f"{T(req, 'cheques')} · {cheque['number']}", req.user['id'], time.time(), cid))
    treasury_delta(req.db, treasury_id, cheque['amount'] if incoming else -cheque['amount'])
    if cheque['party_id']:
        party_delta(req.db, cheque['party_type'], cheque['party_id'], -cheque['amount'])
    ex(req.db, "UPDATE cheques SET status='cleared', treasury_id=? WHERE id=?", (treasury_id, cid))
    audit(req, 'update', 'cheques', cid, 'cleared')
    flash(req, T(req, 'cheque_cleared'), 'success')
    return redirect(url_for('cheques'), req)


@router.route('cheque_bounce', '/cheques/<cid>/bounce', ('POST',))
def cheque_bounce(req, cid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    cheque = one(req.db, 'SELECT * FROM cheques WHERE id=?', (cid,))
    if not cheque:
        return core.not_found()
    if cheque['status'] == 'cleared':
        flash(req, T(req, 'cheque_cleared'), 'info')
        return redirect(url_for('cheques'), req)
    ex(req.db, "UPDATE cheques SET status='bounced' WHERE id=?", (cid,))
    audit(req, 'update', 'cheques', cid, 'bounced')
    flash(req, T(req, 'cheque_bounced'), 'warning')
    return redirect(url_for('cheques'), req)


# --------------------------------------------------------------------------- #
# installments
# --------------------------------------------------------------------------- #
def _plan_amounts(total, count, start_date, period):
    count = max(1, int(count or 1))
    amount = round(float(total) / count, 2)
    rows = []
    running = 0.0
    for index in range(count):
        value = amount if index < count - 1 else round(float(total) - running, 2)
        running = round(running + value, 2)
        rows.append((db.add_period(start_date, period, index), value))
    return rows


@router.route('installments', '/installments', ('GET', 'POST'))
def installments(req):
    blocked = guard(req, 'p_inst')
    if blocked:
        return blocked
    if req.method == 'POST':
        customer_id = req.i('customer_id')
        total = core.safe_float(req.f('total'))
        count = max(1, core.safe_int(req.f('count'), 1))
        period = req.f('period') or 'month'
        start = req.f('start_date') or today()
        down = core.safe_float(req.f('down_payment'))
        if not customer_id or total <= 0:
            flash(req, T(req, 'required_field'), 'danger')
            return redirect(url_for('installments'), req)
        plan_id = x(req.db, '''INSERT INTO installment_plans(customer_id,invoice_id,total,count,period,
                              start_date,notes,user_id,ctime,down_payment) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (customer_id, req.i('invoice_id'), total, count, period, start, 
                    (req.f('notes') or '').strip(), req.user['id'], time.time(), down))
        for due_date, amount in _plan_amounts(total, count, start, period):
            x(req.db, 'INSERT INTO installments(plan_id,due_date,amount) VALUES(?,?,?)',
              (plan_id, due_date, amount))
        audit(req, 'create', 'installment_plans', plan_id)
        flash(req, T(req, 'plan_created'), 'success')
        return redirect(url_for('installment_plan_view', pid=plan_id), req)
    plans = q(req.db, '''SELECT p.*, c.name customer_name,
                                (SELECT COUNT(*) FROM installments i WHERE i.plan_id=p.id AND i.paid=0) remaining,
                                (SELECT COALESCE(SUM(i.amount),0) FROM installments i
                                 WHERE i.plan_id=p.id AND i.paid=0) remaining_amount,
                                (SELECT COUNT(*) FROM installments i WHERE i.plan_id=p.id) installments
                         FROM installment_plans p JOIN customers c ON c.id=p.customer_id
                         ORDER BY p.id DESC LIMIT 200''')
    due = q(req.db, '''SELECT i.*, c.name customer_name, p.id plan_id FROM installments i
                       JOIN installment_plans p ON p.id=i.plan_id
                       JOIN customers c ON c.id=p.customer_id
                       WHERE i.paid=0 AND i.due_date<=? ORDER BY i.due_date LIMIT 100''', (today(),))
    customers = q(req.db, 'SELECT id, name, balance FROM customers WHERE active=1 ORDER BY name')
    invoices = q(req.db, "SELECT id, number, net, party_id FROM invoices WHERE itype='sale' "
                         "AND status IN ('credit','partial') ORDER BY id DESC LIMIT 200")
    return render(req, 'installments.html', title=T(req, 'installment_plans'), active='installments',
                  plans=plans, due=due, customers=customers, invoices=invoices,
                  treasuries=scoped_treasuries(req), today=today())


@router.route('installment_plan_view', '/installments/plan/<pid>', ('GET',))
def installment_plan_view(req, pid):
    blocked = guard(req, 'p_inst')
    if blocked:
        return blocked
    plan = one(req.db, '''SELECT p.*, c.name customer_name, c.phone, c.balance customer_balance
                          FROM installment_plans p JOIN customers c ON c.id=p.customer_id
                          WHERE p.id=?''', (pid,))
    if not plan:
        return core.not_found()
    rows = q(req.db, 'SELECT * FROM installments WHERE plan_id=? ORDER BY due_date', (pid,))
    paid = sum(core.safe_float(r['amount']) for r in rows if r['paid'])
    return render(req, 'installment_plan.html', title=T(req, 'plan_schedule'), active='installments',
                  plan=plan, rows=rows, paid=paid, remaining=core.safe_float(plan['total']) - paid,
                  treasuries=scoped_treasuries(req), today=today())


@router.route('installment_pay', '/installments/<iid>/pay', ('POST',))
def installment_pay(req, iid):
    blocked = guard(req, 'p_inst')
    if blocked:
        return blocked
    row = one(req.db, '''SELECT i.*, p.customer_id, p.id plan_id FROM installments i
                         JOIN installment_plans p ON p.id=i.plan_id WHERE i.id=?''', (iid,))
    if not row:
        return core.not_found()
    if row['paid']:
        flash(req, T(req, 'installment_paid_msg'), 'info')
        return redirect(url_for('installment_plan_view', pid=row['plan_id']), req)
    amount = min(core.safe_float(req.f('amount')) or core.safe_float(row['amount']),
                 core.safe_float(row['amount']))
    treasury_id = req.i('treasury_id') or default_treasury(req)
    number = next_number(req, 'voucher')
    voucher_id = x(req.db, '''INSERT INTO vouchers(number,vtype,treasury_id,party_type,party_id,amount,
                              vdate,note,user_id,ctime) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (number, 'receipt', treasury_id, 'customer', row['customer_id'], amount, today(),
                    f"{T(req, 'installment_amount')} #{iid}", req.user['id'], time.time()))
    treasury_delta(req.db, treasury_id, amount)
    party_delta(req.db, 'customer', row['customer_id'], -amount)
    paid_amount = round(core.safe_float(row['paid_amount']) + amount, 2)
    fully = paid_amount >= core.safe_float(row['amount']) - 0.005
    ex(req.db, 'UPDATE installments SET paid=?, paid_date=?, paid_amount=?, treasury_id=? WHERE id=?',
       (1 if fully else 0, today(), paid_amount, treasury_id, iid))
    audit(req, 'update', 'installments', iid, f'paid {amount}')
    flash(req, T(req, 'installment_paid_msg'), 'success')
    return redirect(url_for('installment_plan_view', pid=row['plan_id']), req)


@router.route('installment_due', '/installments/due', ('GET',))
def installment_due(req):
    blocked = guard(req, 'p_inst')
    if blocked:
        return blocked
    rows = q(req.db, '''SELECT i.*, c.name customer_name, c.phone, p.period, p.total plan_total
                        FROM installments i JOIN installment_plans p ON p.id=i.plan_id
                        JOIN customers c ON c.id=p.customer_id
                        WHERE i.paid=0 ORDER BY i.due_date''')
    overdue = [row for row in rows if row['due_date'] < today()]
    upcoming = [row for row in rows if row['due_date'] >= today()]
    if req.q('export'):
        header = [T(req, 'customer'), T(req, 'phone'), T(req, 'installment_amount'), T(req, 'due_date'),
                  T(req, 'status')]
        data = [header] + [[row['customer_name'], row['phone'], row['amount'], row['due_date'],
                            T(req, 'overdue')] for row in overdue]
        return export_rows('overdue-installments.xlsx', data)
    total_overdue = sum(core.safe_float(row['amount']) for row in overdue)
    return render(req, 'installment_due.html', title=T(req, 'due_installments'), active='installment_due',
                  overdue=overdue, upcoming=upcoming, total_overdue=total_overdue, today=today())


# --------------------------------------------------------------------------- #
# service order payment
# --------------------------------------------------------------------------- #
@router.route('service_collect', '/services/<rid>/collect', ('POST',))
def service_collect(req, rid):
    blocked = guard(req, 'p_services')
    if blocked:
        return blocked
    order = one(req.db, 'SELECT * FROM service_orders WHERE id=?', (rid,))
    if not order:
        return core.not_found()
    amount = core.safe_float(req.f('amount'))
    if amount <= 0:
        flash(req, T(req, 'required_field'), 'danger')
        return redirect(url_for('services'), req)
    treasury_id = req.i('treasury_id') or default_treasury(req)
    number = next_number(req, 'voucher')
    x(req.db, '''INSERT INTO vouchers(number,vtype,treasury_id,party_type,party_id,amount,vdate,note,
                 user_id,ctime) VALUES(?,?,?,?,?,?,?,?,?,?)''',
      (number, 'receipt', treasury_id, '', 0, amount, today(),
       f"{T(req, 'service_orders')} · {order['customer_name']}", req.user['id'], time.time()))
    treasury_delta(req.db, treasury_id, amount)
    ex(req.db, 'UPDATE service_orders SET paid=paid+? WHERE id=?', (amount, rid))
    audit(req, 'update', 'service_orders', rid, f'collected {amount}')
    flash(req, T(req, 'payment_saved'), 'success')
    return redirect(url_for('services'), req)
