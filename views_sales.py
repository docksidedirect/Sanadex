# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - sales & purchase invoices, orders, payments."""
import json
import time

import core
from core import (T, audit, base_qty, can, compute_price, consume_batches, customer_tier_percent,
                  export_rows, flash, guard, money, next_number, one, q, render, scalar, today,
                  x, party_delta, treasury_delta, movement, add_batch, restore_batch,
                  scoped_stores, scoped_treasuries, default_store, default_treasury,
                  active_offers, stock_of)
from db import ex
from framework import redirect, router, url_for


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _party_table(itype):
    return 'customers' if itype == 'sale' else 'suppliers'


def invoice_form_data(req, itype, prefill=None):
    conn = req.db
    items = q(conn, 'SELECT id, code, name, name_en, sale_price, purchase_price, has_expiry, reorder_level '
                    'FROM items WHERE active=1 ORDER BY name')
    for item in items:
        item['units'] = q(conn, 'SELECT id, name, factor, barcode, sale_price, purchase_price '
                                'FROM item_units WHERE item_id=? ORDER BY factor DESC', (item['id'],))
    table = _party_table(itype)
    parties = q(conn, f'''SELECT p.id, p.name, p.phone, p.balance,
                                 COALESCE(t.percent,0) tier_percent, p.credit_limit
                          FROM {table} p LEFT JOIN price_tiers t ON t.id=p.tier_id
                          WHERE p.active=1 ORDER BY p.name''') if itype == 'sale' else \
              q(conn, f'SELECT p.id, p.name, p.phone, p.balance, 0 tier_percent, 0 credit_limit '
                      f'FROM {table} p WHERE p.active=1 ORDER BY p.name')
    offers = active_offers(conn)
    return dict(items=items, parties=parties, stores=scoped_stores(req), treasuries=scoped_treasuries(req),
                default_store=default_store(req), default_treasury=default_treasury(req),
                tax_percent=core.safe_float((req.company or {}).get('tax_percent')),
                offers=json.dumps({str(k): v for k, v in offers.items()}),
                items_json=json.dumps(items, ensure_ascii=False, default=str),
                parties_json=json.dumps(parties, ensure_ascii=False, default=str),
                prefill_json=json.dumps(prefill or {}, ensure_ascii=False, default=str),
                itype=itype, prefill=prefill or {},
                active='sale_invoices' if itype == 'sale' else 'purchase_invoices')


@router.route('sale_invoice_new', '/invoice/sale/new', ('GET', 'POST'))
@router.route('purchase_invoice_new', '/invoice/purchase/new', ('GET', 'POST'))
def invoice_new(req):
    itype = 'sale' if '/sale/' in req.path else 'purchase'
    blocked = guard(req, 'p_sell' if itype == 'sale' else 'p_buy')
    if blocked:
        return blocked
    if req.method == 'POST':
        return invoice_save(req, itype)
    prefill = {}
    duplicate = req.q('duplicate')
    if duplicate:
        invoice = one(req.db, 'SELECT * FROM invoices WHERE id=?', (duplicate,))
        if invoice and invoice['itype'] == itype:
            prefill = dict(invoice)
            prefill['lines'] = [{'item_id': line['item_id'], 'unit_id': line['unit_id'],
                                 'qty': line['qty'], 'price': line['price'], 'manual': 1,
                                 'expiry': line['expiry_date']}
                                for line in q(req.db, 'SELECT * FROM invoice_items WHERE invoice_id=?',
                                              (duplicate,))]
    return render(req, 'invoice_form.html',
                  title=T(req, 'new_sale_invoice' if itype == 'sale' else 'new_purchase_invoice'),
                  **invoice_form_data(req, itype, prefill))


def invoice_save(req, itype):
    conn = req.db
    company = req.company
    party_type = 'customer' if itype == 'sale' else 'supplier'
    party_id = req.i('party_id')
    store_id = req.i('store_id') or default_store(req)
    treasury_id = req.i('treasury_id')
    idate = req.f('idate') or today()
    due_date = req.f('due_date') or ''
    is_tax = 1 if req.f('is_tax') else 0
    tax_percent = core.safe_float(req.f('tax_percent'), core.safe_float(company.get('tax_percent')))
    discount = core.safe_float(req.f('discount'))
    paid_input = core.safe_float(req.f('paid'))

    item_ids = req.flist('item_id')
    unit_ids = req.flist('unit_id')
    qties = req.flist('qty')
    prices = req.flist('price')
    manuals = req.flist('manual')
    expiries = req.flist('expiry')
    tier = customer_tier_percent(conn, party_id) if itype == 'sale' else 0.0
    offers = active_offers(conn) if itype == 'sale' else {}

    lines, errors = [], []
    needed = {}
    for index, item_id in enumerate(item_ids):
        if not item_id or not str(item_id).isdigit():
            continue
        qty_raw = qties[index] if index < len(qties) else ''
        if str(qty_raw).strip() == '':
            continue
        item = one(conn, 'SELECT * FROM items WHERE id=?', (item_id,))
        if not item:
            continue
        unit_id = unit_ids[index] if index < len(unit_ids) else ''
        unit = one(conn, 'SELECT * FROM item_units WHERE id=?', (unit_id,)) or \
            one(conn, 'SELECT * FROM item_units WHERE item_id=? ORDER BY factor DESC LIMIT 1', (item_id,)) or {}
        unit_id = unit.get('id') or 0
        qty = core.safe_float(qty_raw)
        if qty <= 0:
            errors.append(T(req, 'qty') + ': ' + T(req, 'required_field'))
            continue
        base_price = core.safe_float(unit.get('sale_price' if itype == 'sale' else 'purchase_price')) or \
            core.safe_float(item.get('sale_price' if itype == 'sale' else 'purchase_price'))
        manual = index < len(manuals) and str(manuals[index]) in ('1', 'on', 'true')
        posted = core.safe_float(prices[index] if index < len(prices) else 0)
        if itype == 'purchase':
            # what the supplier charged is authoritative; fall back to the master cost
            price = posted if posted > 0 else base_price
        elif manual and posted > 0:
            price = posted
        else:
            offer = float(offers.get(int(item_id)) or offers.get('all') or 0)
            price = compute_price(base_price, tier, offer)
            if price <= 0 and posted > 0:
                price = posted
        cost = core.safe_float(item.get('purchase_price'))
        factor = core.safe_float(unit.get('factor'), 1) or 1
        unit_cost = cost * factor
        if itype == 'purchase':
            # cost of this bought unit is exactly what we paid for it
            unit_cost = price
        expiry = (expiries[index] if index < len(expiries) else '') or ''
        total = round(qty * price, 2)
        lines.append({'item_id': int(item_id), 'unit_id': int(unit_id), 'qty': qty, 'price': price,
                      'cost': round(unit_cost, 4), 'total': total, 'expiry': expiry,
                      'base_qty': qty * factor, 'tier': tier, 'name': item['name']})
        needed[int(item_id)] = needed.get(int(item_id), 0) + qty * factor

    if not lines and not errors:
        errors.append(T(req, 'add_at_least_one_line'))
    if itype == 'sale' and store_id:
        for item_id, required_qty in needed.items():
            available = stock_of(conn, item_id, store_id)
            if available + 1e-9 < required_qty:
                name = core.item_name(conn, item_id)
                errors.append(T(req, 'not_enough_stock', name=name))
    if errors:
        for message in errors:
            flash(req, message, 'danger')
        prefill = {'party_id': party_id, 'store_id': store_id, 'treasury_id': treasury_id,
                   'idate': idate, 'due_date': due_date, 'is_tax': is_tax,
                   'discount': discount, 'paid': paid_input, 'notes': req.f('notes'),
                   'ref': req.f('ref'), 'lines': lines}
        return render(req, 'invoice_form.html',
                      title=T(req, 'new_sale_invoice' if itype == 'sale' else 'new_purchase_invoice'),
                      **invoice_form_data(req, itype, prefill))

    subtotal = round(sum(line['total'] for line in lines), 2)
    taxable = max(0.0, round(subtotal - discount, 2))
    tax = round(taxable * tax_percent / 100, 2) if is_tax else 0.0
    net = round(taxable + tax, 2)
    paid = max(0.0, min(paid_input, net))
    status = 'paid' if paid >= net - 0.005 else ('partial' if paid > 0 else 'credit')
    number = next_number(req, itype)
    invoice_id = x(conn, '''INSERT INTO invoices(number,itype,idate,due_date,party_type,party_id,store_id,
                            treasury_id,subtotal,discount,tax,tax_percent,net,paid,status,notes,ref,user_id,
                            is_tax,ctime) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (number, itype, idate, due_date, party_type, party_id, store_id, treasury_id,
                    subtotal, discount, tax, tax_percent, net, paid, status, (req.f('notes') or '').strip(),
                    (req.f('ref') or '').strip(), req.user['id'], is_tax, time.time()))
    sign = -1 if itype == 'sale' else 1
    for line in lines:
        x(conn, 'INSERT INTO invoice_items(invoice_id,item_id,unit_id,qty,price,cost,discount,total,'
                'expiry_date,tier_pct) VALUES(?,?,?,?,?,?,?,?,?,?)',
          (invoice_id, line['item_id'], line['unit_id'], line['qty'], line['price'], line['cost'], 0,
           line['total'], line['expiry'], line['tier']))
        movement(conn, line['item_id'], line['unit_id'], store_id, sign * line['base_qty'],
                 f'{itype}_invoice', invoice_id, number, idate, req.user['id'])
        if itype == 'purchase':
            add_batch(conn, line['item_id'], store_id, line['base_qty'], line['expiry'])
            if line['price'] > 0:
                ex(conn, 'UPDATE items SET purchase_price=? WHERE id=?',
                   (round(line['price'] / (line['base_qty'] / line['qty']), 4), line['item_id']))
        else:
            consume_batches(conn, line['item_id'], store_id, line['base_qty'])
    if paid and treasury_id:
        treasury_delta(conn, treasury_id, paid if itype == 'sale' else -paid)
    if party_id:
        party_delta(conn, party_type, party_id, net - paid)
        if itype == 'sale':
            limit = scalar(conn, 'SELECT credit_limit FROM customers WHERE id=?', (party_id,))
            if limit and core.safe_float(limit) > 0:
                balance = scalar(conn, 'SELECT balance FROM customers WHERE id=?', (party_id,))
                if core.safe_float(balance) > core.safe_float(limit):
                    flash(req, T(req, 'credit_limit_warning',
                                 name=core.party_name(conn, 'customer', party_id),
                                 limit=core.money(limit)), 'warning')
    audit(req, 'create', 'invoices', invoice_id, f'{number} {net}')
    flash(req, T(req, 'invoice_saved', number=number), 'success')
    return redirect(url_for('invoice_print', iid=invoice_id), req)


def _invoice_query(req, itype):
    where = ['v.itype=?']
    args = [itype]
    status = req.q('status')
    if status:
        where.append('v.status=?')
        args.append(status)
    if req.q('party'):
        where.append('COALESCE(c.name, s.name) LIKE ?')
        args.append(f"%{req.q('party')}%")
    if req.q('s'):
        where.append('v.number LIKE ?')
        args.append(f"%{req.q('s')}%")
    if req.q('d1'):
        where.append('v.idate>=?')
        args.append(req.q('d1'))
    if req.q('d2'):
        where.append('v.idate<=?')
        args.append(req.q('d2'))
    from_sql = ('FROM invoices v LEFT JOIN customers c ON c.id=v.party_id AND v.party_type=\'customer\' '
                'LEFT JOIN suppliers s ON s.id=v.party_id AND v.party_type=\'supplier\' '
                'LEFT JOIN users u ON u.id=v.user_id '
                'WHERE ' + ' AND '.join(where))
    return from_sql, args


@router.route('sale_invoices', '/invoices/sale', ('GET',))
@router.route('purchase_invoices', '/invoices/purchase', ('GET',))
def invoice_list(req):
    itype = 'sale' if req.path.endswith('sale') else 'purchase'
    blocked = guard(req, 'p_sell' if itype == 'sale' else 'p_buy')
    if blocked:
        return blocked
    where, args = _invoice_query(req, itype)
    page, per, offset = core.page_params(req, 40)
    total = scalar(req.db, f'SELECT COUNT(*) c {where}', args)
    rows = q(req.db, f'''SELECT v.*, COALESCE(c.name, s.name) party_name, u.full_name username
                         {where} ORDER BY v.id DESC LIMIT ? OFFSET ?''', args + [per, offset])
    totals = one(req.db, f'''SELECT COALESCE(SUM(v.net),0) net, COALESCE(SUM(v.paid),0) paid,
                                    COALESCE(SUM(v.tax),0) tax {where}''', args) or {}
    export = req.q('export')
    if export:
        header = ['number', 'date', 'party', 'subtotal', 'discount', 'tax', 'net', 'paid', 'status']
        data = [[T(req, 'invoice_no'), T(req, 'date'), T(req, 'party'), T(req, 'subtotal'),
                 T(req, 'discount'), T(req, 'tax'), T(req, 'net'), T(req, 'paid'), T(req, 'status')]]
        for row in q(req.db, f'SELECT v.*, COALESCE(c.name, s.name) party_name {where} ORDER BY v.id DESC',
                     args):
            data.append([row['number'], row['idate'], row['party_name'] or '', row['subtotal'],
                         row['discount'], row['tax'], row['net'], row['paid'],
                         T(req, 'status_' + row['status'])])
        return export_rows(f'{itype}-invoices.xlsx', data)
    return render(req, 'invoice_list.html',
                  title=T(req, 'sales_invoices' if itype == 'sale' else 'purchase_invoices'),
                  active='sale_invoices' if itype == 'sale' else 'purchase_invoices',
                  rows=rows, itype=itype, totals=totals,
                  pager=core.pager(req, total, page, per), status=req.q('status') or '')


@router.route('invoice_view', '/invoice/<iid>', ('GET',))
@router.route('invoice_print', '/invoice/<iid>/print', ('GET',))
def invoice_print(req, iid):
    blocked = guard(req)
    if blocked:
        return blocked
    invoice = one(req.db, 'SELECT * FROM invoices WHERE id=?', (iid,))
    if not invoice:
        return core.not_found()
    party = one(req.db, f'SELECT * FROM {_party_table(invoice["itype"])} WHERE id=?',
                (invoice['party_id'],)) if invoice['party_id'] else {}
    lines = q(req.db, '''SELECT ii.*, i.name item_name, i.code item_code, u.name unit_name
                         FROM invoice_items ii JOIN items i ON i.id=ii.item_id
                         LEFT JOIN item_units u ON u.id=ii.unit_id WHERE ii.invoice_id=?''', (iid,))
    store = one(req.db, 'SELECT * FROM stores WHERE id=?', (invoice['store_id'],)) or {}
    treasury = one(req.db, 'SELECT * FROM treasuries WHERE id=?', (invoice['treasury_id'],)) or {}
    user = one(req.db, 'SELECT full_name, username FROM users WHERE id=?', (invoice['user_id'],)) or {}
    size = req.q('size', 'a4')
    payments = q(req.db, "SELECT * FROM vouchers WHERE party_type=? AND party_id=? AND note LIKE ?",
                 (invoice['party_type'], invoice['party_id'], f"%{invoice['number']}%"))
    is_print = req.path.endswith('/print')
    ctx = dict(title=f"{T(req, 'invoice')} {invoice['number']}",
               active='invoice_print', inv=invoice, party=party, lines=lines, store=store,
               treasury=treasury, user=user, size=size, payments=payments,
               remaining=round(core.safe_float(invoice['net']) - core.safe_float(invoice['paid']), 2),
               treasuries=scoped_treasuries(req))
    return render(req, 'invoice_print.html' if is_print else 'invoice_view.html', **ctx)


@router.route('invoice_cancel', '/invoice/<iid>/cancel', ('POST',))
def invoice_cancel(req, iid):
    blocked = guard(req, 'p_sell')
    if blocked:
        return blocked
    invoice = one(req.db, 'SELECT * FROM invoices WHERE id=?', (iid,))
    if not invoice:
        return core.not_found()
    if invoice['status'] != 'cancelled':
        sign = 1 if invoice['itype'] == 'sale' else -1
        for line in q(req.db, 'SELECT * FROM invoice_items WHERE invoice_id=?', (iid,)):
            base = base_qty(req.db, line['unit_id'], line['qty'])
            movement(req.db, line['item_id'], line['unit_id'], invoice['store_id'], sign * base,
                     'cancel', iid, T(req, 'cancelled') + ' ' + invoice['number'], today(), req.user['id'])
            if invoice['itype'] == 'sale':
                restore_batch(req.db, line['item_id'], invoice['store_id'], base,
                              line.get('expiry_date') or '')
            else:
                ex(req.db, 'UPDATE item_batches SET qty=MAX(qty-?,0) WHERE item_id=? AND store_id=? AND expiry_date=?',
                   (base, line['item_id'], invoice['store_id'], line.get('expiry_date') or ''))
        if invoice['treasury_id'] and invoice['paid']:
            treasury_delta(req.db, invoice['treasury_id'],
                           -invoice['paid'] if invoice['itype'] == 'sale' else invoice['paid'])
        if invoice['party_id']:
            party_delta(req.db, invoice['party_type'], invoice['party_id'],
                        -(invoice['net'] - invoice['paid']))
        ex(req.db, "UPDATE invoices SET status='cancelled' WHERE id=?", (iid,))
        audit(req, 'update', 'invoices', iid, 'cancelled')
        flash(req, T(req, 'invoice_cancelled'), 'warning')
    return redirect(url_for('sale_invoices' if invoice['itype'] == 'sale' else 'purchase_invoices'), req)


@router.route('invoice_pay', '/invoice/<iid>/pay', ('POST',))
def invoice_pay(req, iid):
    blocked = guard(req, 'p_treasury')
    if blocked:
        return blocked
    invoice = one(req.db, 'SELECT * FROM invoices WHERE id=?', (iid,))
    if not invoice:
        return core.not_found()
    remaining = round(core.safe_float(invoice['net']) - core.safe_float(invoice['paid']), 2)
    if remaining <= 0.005:
        flash(req, T(req, 'invoice_paid'), 'info')
        return redirect(url_for('invoice_view', iid=iid), req)
    amount = min(core.safe_float(req.f('amount')), remaining)
    if amount <= 0:
        flash(req, T(req, 'required_field'), 'danger')
        return redirect(url_for('invoice_view', iid=iid), req)
    treasury_id = req.i('treasury_id') or default_treasury(req)
    vdate = req.f('vdate') or today()
    sign = 1 if invoice['itype'] == 'sale' else -1
    number = next_number(req, 'voucher')
    voucher_id = x(req.db, '''INSERT INTO vouchers(number,vtype,treasury_id,party_type,party_id,amount,
                              vdate,note,user_id,ctime) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (number, 'receipt' if invoice['itype'] == 'sale' else 'payment', treasury_id,
                    invoice['party_type'], invoice['party_id'], amount, vdate,
                    f"{T(req, 'invoice')} {invoice['number']}", req.user['id'], time.time()))
    treasury_delta(req.db, treasury_id, sign * amount)
    party_delta(req.db, invoice['party_type'], invoice['party_id'], -sign * amount)
    paid = round(core.safe_float(invoice['paid']) + amount, 2)
    status = 'paid' if paid >= core.safe_float(invoice['net']) - 0.005 else 'partial'
    ex(req.db, 'UPDATE invoices SET paid=?, status=? WHERE id=?', (paid, status, iid))
    audit(req, 'create', 'vouchers', voucher_id, f"invoice payment {invoice['number']}")
    flash(req, T(req, 'payment_saved'), 'success')
    return redirect(url_for('invoice_view', iid=iid), req)


@router.route('invoice_duplicate', '/invoice/<iid>/duplicate', ('GET',))
def invoice_duplicate(req, iid):
    invoice = one(req.db, 'SELECT * FROM invoices WHERE id=?', (iid,))
    if not invoice:
        return core.not_found()
    target = 'sale_invoice_new' if invoice['itype'] == 'sale' else 'purchase_invoice_new'
    return redirect(url_for(target) + f'?duplicate={iid}', req)


# --------------------------------------------------------------------------- #
# orders
# --------------------------------------------------------------------------- #
@router.route('sale_orders', '/orders/sale', ('GET',))
@router.route('purchase_orders', '/orders/purchase', ('GET',))
def order_list(req):
    otype = 'sale' if req.path.endswith('sale') else 'purchase'
    blocked = guard(req, 'p_sell' if otype == 'sale' else 'p_buy')
    if blocked:
        return blocked
    table = _party_table(otype)
    rows = q(req.db, f'''SELECT o.*, p.name party_name,
                                (SELECT COUNT(*) FROM doc_order_items i WHERE i.order_id=o.id) lines,
                                (SELECT COALESCE(SUM(i.qty*i.price),0) FROM doc_order_items i
                                 WHERE i.order_id=o.id) total
                         FROM doc_orders o LEFT JOIN {table} p ON p.id=o.party_id
                         WHERE o.otype=? ORDER BY o.id DESC LIMIT 400''', (otype,))
    return render(req, 'order_list.html',
                  title=T(req, 'sale_orders' if otype == 'sale' else 'purchase_orders'),
                  active='sale_orders' if otype == 'sale' else 'purchase_orders',
                  rows=rows, otype=otype, day=today())


@router.route('order_new', '/orders/new', ('GET', 'POST'))
def order_new(req):
    otype = req.q('type') or req.f('otype') or 'sale'
    otype = 'purchase' if otype in ('purchase', 'p_buy') else 'sale'
    blocked = guard(req, 'p_sell' if otype == 'sale' else 'p_buy')
    if blocked:
        return blocked
    if req.method == 'POST':
        party_id = req.i('party_id')
        number = next_number(req, 'order')
        oid = x(req.db, '''INSERT INTO doc_orders(number,otype,party_id,odate,due_date,status,notes,
                           user_id,ctime) VALUES(?,?,?,?,?,?,?,?,?)''',
                (number, otype, party_id, req.f('odate') or today(), req.f('due_date') or '',
                 'pending', (req.f('notes') or '').strip(), req.user['id'], time.time()))
        count = 0
        for item_id, unit_id, qty, price in zip(req.flist('item_id'), req.flist('unit_id'),
                                                req.flist('qty'), req.flist('price')):
            if item_id and str(qty).strip():
                x(req.db, 'INSERT INTO doc_order_items(order_id,item_id,unit_id,qty,price) '
                         'VALUES(?,?,?,?,?)',
                  (oid, item_id, unit_id, core.safe_float(qty), core.safe_float(price)))
                count += 1
        if not count:
            ex(req.db, 'DELETE FROM doc_orders WHERE id=?', (oid,))
            flash(req, T(req, 'add_at_least_one_line'), 'danger')
            return redirect(url_for('order_new') + f'?type={otype}', req)
        audit(req, 'create', 'doc_orders', oid, number)
        flash(req, T(req, 'order_saved', number=number), 'success')
        return redirect(url_for('sale_orders' if otype == 'sale' else 'purchase_orders'), req)
    data = invoice_form_data(req, otype)
    return render(req, 'order_form.html',
                  title=T(req, 'new_order'), active='sale_orders' if otype == 'sale' else 'purchase_orders',
                  **data)


@router.route('order_done', '/orders/<oid>/done', ('POST',))
def order_done(req, oid):
    blocked = guard(req, 'p_sell')
    if blocked:
        return blocked
    order = one(req.db, 'SELECT * FROM doc_orders WHERE id=?', (oid,))
    if order:
        ex(req.db, "UPDATE doc_orders SET status='done' WHERE id=?", (oid,))
        audit(req, 'update', 'doc_orders', oid, 'done')
        flash(req, T(req, 'saved'), 'success')
    return redirect(url_for('sale_orders' if order and order['otype'] == 'sale' else 'purchase_orders'), req)


@router.route('order_delete', '/orders/<oid>/delete', ('POST',))
def order_delete(req, oid):
    blocked = guard(req, 'p_sell')
    if blocked:
        return blocked
    order = one(req.db, 'SELECT * FROM doc_orders WHERE id=?', (oid,))
    if order:
        ex(req.db, 'DELETE FROM doc_order_items WHERE order_id=?', (oid,))
        ex(req.db, 'DELETE FROM doc_orders WHERE id=?', (oid,))
        audit(req, 'delete', 'doc_orders', oid)
        flash(req, T(req, 'deleted'), 'warning')
    return redirect(url_for('sale_orders' if order and order['otype'] == 'sale' else 'purchase_orders'), req)


@router.route('order_convert', '/orders/<oid>/convert', ('POST',))
def order_convert(req, oid):
    order = one(req.db, 'SELECT * FROM doc_orders WHERE id=?', (oid,))
    if not order:
        return core.not_found()
    blocked = guard(req, 'p_sell' if order['otype'] == 'sale' else 'p_buy')
    if blocked:
        return blocked
    if order.get('invoice_id'):
        existing = one(req.db, 'SELECT id, number FROM invoices WHERE id=?', (order['invoice_id'],))
        if existing:
            flash(req, T(req, 'converted_already', number=existing['number']), 'info')
            return redirect(url_for('invoice_view', iid=existing['id']), req)
    itype = order['otype']
    store_id = default_store(req)
    number = next_number(req, itype)
    subtotal = 0.0
    lines = q(req.db, 'SELECT * FROM doc_order_items WHERE order_id=?', (oid,))
    for line in lines:
        subtotal += round(core.safe_float(line['qty']) * core.safe_float(line['price']), 2)
    invoice_id = x(req.db, '''INSERT INTO invoices(number,itype,idate,party_type,party_id,store_id,
                              treasury_id,subtotal,discount,tax,tax_percent,net,paid,status,notes,ref,
                              user_id,is_tax,ctime,order_id)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                   (number, itype, today(), 'customer' if itype == 'sale' else 'supplier',
                    order['party_id'], store_id, 0, subtotal, 0, 0, 0, subtotal, 0, 'credit',
                    T(req, 'invoice_from_order', number=order['number']), order['number'],
                    req.user['id'], 0, time.time(), oid))
    for line in lines:
        x(req.db, 'INSERT INTO invoice_items(invoice_id,item_id,unit_id,qty,price,cost,total) '
                 'VALUES(?,?,?,?,?,?,?)',
          (invoice_id, line['item_id'], line['unit_id'], line['qty'], line['price'],
           core.safe_float((one(req.db, 'SELECT purchase_price p FROM items WHERE id=?',
                                (line['item_id'],)) or {}).get('p')), 
           round(core.safe_float(line['qty']) * core.safe_float(line['price']), 2)))
        base = base_qty(req.db, line['unit_id'], line['qty'])
        movement(req.db, line['item_id'], line['unit_id'], store_id,
                 -base if itype == 'sale' else base, f'{itype}_invoice', invoice_id, number,
                 today(), req.user['id'])
        if itype == 'purchase':
            add_batch(req.db, line['item_id'], store_id, base, '')
        else:
            consume_batches(req.db, line['item_id'], store_id, base)
    if order['party_id']:
        party_delta(req.db, 'customer' if itype == 'sale' else 'supplier', order['party_id'], subtotal)
    ex(req.db, "UPDATE doc_orders SET status='done', invoice_id=? WHERE id=?", (invoice_id, oid))
    audit(req, 'create', 'invoices', invoice_id, f'from order {order["number"]}')
    flash(req, T(req, 'invoice_saved', number=number), 'success')
    return redirect(url_for('invoice_print', iid=invoice_id), req)


@router.route('order_print', '/orders/<oid>/print', ('GET',))
def order_print(req, oid):
    blocked = guard(req)
    if blocked:
        return blocked
    order = one(req.db, 'SELECT * FROM doc_orders WHERE id=?', (oid,))
    if not order:
        return core.not_found()
    table = _party_table(order['otype'])
    party = one(req.db, f'SELECT * FROM {table} WHERE id=?', (order['party_id'],)) if order['party_id'] else {}
    lines = q(req.db, '''SELECT i.*, it.name item_name, u.name unit_name FROM doc_order_items i
                         JOIN items it ON it.id=i.item_id LEFT JOIN item_units u ON u.id=i.unit_id
                         WHERE i.order_id=?''', (oid,))
    total = sum(core.safe_float(l['qty']) * core.safe_float(l['price']) for l in lines)
    return render(req, 'order_print.html', title=f"{T(req, 'order_no')} {order['number']}",
                  order=order, party=party, lines=lines, total=total, active='order_print')
