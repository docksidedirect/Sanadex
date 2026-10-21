# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - items, units, stock operations, labels, Excel bridge."""
import csv
import io
import os
import time

import barcode as barcode_lib
import core
import db
import xlsx
from core import (T, audit, can, export_rows, flash, guard, money, movement, one, q, render,
                  scalar, today, x)
from db import ex
from framework import file_response, redirect, router, url_for


def _item_row(req, rid):
    return one(req.db, 'SELECT * FROM items WHERE id=?', (rid,))


def _units(req, item_id):
    return q(req.db, 'SELECT * FROM item_units WHERE item_id=? ORDER BY factor DESC, id', (item_id,))


def _save_units(req, item_id, fallback_code='', fallback_sale=0, fallback_purchase=0):
    conn = req.db
    ids = req.flist('u_id')
    names = req.flist('u_name')
    factors = req.flist('u_factor')
    barcodes = req.flist('u_barcode')
    sales = req.flist('u_sprice')
    purchases = req.flist('u_pprice')
    keep = []
    for index, name in enumerate(names):
        if not str(name or '').strip():
            continue
        unit_id = ids[index] if index < len(ids) else ''
        factor = core.safe_float(factors[index] if index < len(factors) else 1, 1) or 1
        payload = (str(name).strip(), factor,
                   str(barcodes[index] if index < len(barcodes) else '').strip(),
                   core.safe_float(sales[index] if index < len(sales) else 0),
                   core.safe_float(purchases[index] if index < len(purchases) else 0),
                   1 if abs(factor - 1) < 1e-9 else 0)
        if unit_id and str(unit_id).isdigit():
            ex(conn, 'UPDATE item_units SET name=?, factor=?, barcode=?, sale_price=?, purchase_price=?, '
                     'is_base=? WHERE id=? AND item_id=?', payload + (unit_id, item_id))
            keep.append(int(unit_id))
        else:
            new_id = x(conn, 'INSERT INTO item_units(item_id,name,factor,barcode,sale_price,purchase_price,'
                             'is_base) VALUES(?,?,?,?,?,?,?)', (item_id,) + payload)
            keep.append(new_id)
    for row in q(conn, 'SELECT id FROM item_units WHERE item_id=?', (item_id,)):
        if row['id'] not in keep:
            ex(conn, 'DELETE FROM item_units WHERE id=?', (row['id'],))
    if not q(conn, 'SELECT id FROM item_units WHERE item_id=?', (item_id,)):
        x(conn, 'INSERT INTO item_units(item_id,name,factor,barcode,sale_price,purchase_price,is_base) '
                'VALUES(?,?,?,?,?,?,?)',
          (item_id, T(req, 'piece'), 1, fallback_code or '', fallback_sale, fallback_purchase, 1))
    if not q(conn, 'SELECT id FROM item_units WHERE item_id=? AND is_base=1', (item_id,)):
        ex(conn, 'UPDATE item_units SET is_base=1 WHERE id=(SELECT id FROM item_units WHERE item_id=? '
                 'ORDER BY factor LIMIT 1)', (item_id,))


@router.route('items', '/items', ('GET',))
def items(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    term = (req.q('s') or '').strip()
    category = req.q('cat') or ''
    low = req.q('low')
    expiring = req.q('expiring')
    inactive = req.q('inactive')
    where, args = [], []
    if term:
        where.append('(i.name LIKE ? OR i.code LIKE ? OR i.name_en LIKE ? OR i.brand LIKE ?)')
        args += [f'%{term}%'] * 4
    if category:
        where.append('i.category=?')
        args.append(category)
    if not inactive:
        where.append('i.active=1')
    inner = f'''SELECT i.*, COALESCE((SELECT SUM(m.qty) FROM stock_movements m WHERE m.item_id=i.id),0) stock,
                       (SELECT COUNT(*) FROM item_units u WHERE u.item_id=i.id) units_count,
                       (SELECT MIN(b.expiry_date) FROM item_batches b WHERE b.item_id=i.id AND b.qty>0
                        AND b.expiry_date!='') next_expiry
                FROM items i WHERE {' AND '.join(where) if where else '1=1'}'''
    outer = []
    if low:
        outer.append('reorder_level>0 AND stock<=reorder_level')
    if expiring:
        window = db.add_period(today(), 'day', int(req.company.get('expiry_alert_days') or 30))
        outer.append("next_expiry!='' AND next_expiry<=?")
        args = args + [window]
    clause = 'WHERE ' + ' AND '.join(outer) if outer else ''
    page, per, offset = core.page_params(req, 50)
    total = scalar(req.db, f'SELECT COUNT(*) c FROM ({inner}) t {clause}', args)
    rows = q(req.db, f'SELECT * FROM ({inner}) t {clause} ORDER BY name LIMIT ? OFFSET ?',
            args + [per, offset])
    categories = [r['category'] for r in q(req.db, "SELECT DISTINCT category FROM items WHERE category!='' ORDER BY category")]
    stats = one(req.db, '''SELECT COUNT(*) total,
                                  COALESCE(SUM(CASE WHEN stock<=reorder_level AND reorder_level>0 THEN 1 ELSE 0 END),0) low
                           FROM (SELECT i.id, COALESCE((SELECT SUM(m.qty) FROM stock_movements m
                                   WHERE m.item_id=i.id),0) stock, i.reorder_level
                                 FROM items i WHERE i.active=1) s''') or {}
    return render(req, 'items.html', title=T(req, 'items_title'), subtitle=T(req, 'items_sub'),
                  active='items', rows=rows, total=total, stats=stats, categories=categories,
                  term=term, category=category, low=low, expiring=expiring, inactive=inactive,
                  pager=core.pager(req, total, page, per), today=today())


@router.route('item_new', '/items/new', ('GET', 'POST'))
@router.route('item_edit', '/items/<rid>/edit', ('GET', 'POST'))
def item_form(req, rid=None):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    row = _item_row(req, rid) if rid else None
    if rid and not row:
        return core.not_found()
    if req.method == 'POST':
        data = {
            'code': (req.f('code') or '').strip(),
            'name': (req.f('name') or '').strip(),
            'name_en': (req.f('name_en') or '').strip(),
            'category': (req.f('category') or '').strip(),
            'brand': (req.f('brand') or '').strip(),
            'purchase_price': core.safe_float(req.f('purchase_price')),
            'sale_price': core.safe_float(req.f('sale_price')),
            'min_price': core.safe_float(req.f('min_price')),
            'reorder_level': core.safe_float(req.f('reorder_level')),
            'has_expiry': 1 if req.f('has_expiry') else 0,
            'notes': (req.f('notes') or '').strip(),
            'active': 1 if req.f('active') else 0,
        }
        if not data['name']:
            flash(req, T(req, 'required_field') + ': ' + T(req, 'item_name'), 'danger')
            return render(req, 'item_form.html', title=T(req, 'edit_item' if rid else 'new_item'),
                          active='items', row=dict(data, id=rid or 0), units=[], categories=[])
        if row:
            sets = ', '.join(f'{key}=?' for key in data)
            ex(req.db, f'UPDATE items SET {sets} WHERE id=?', tuple(data.values()) + (rid,))
            item_id = rid
        else:
            item_id = x(req.db, 'INSERT INTO items(code,name,name_en,category,brand,purchase_price,sale_price,'
                               'min_price,reorder_level,has_expiry,notes,active) '
                               'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        tuple(data.values()))
        _save_units(req, item_id, data['code'], data['sale_price'], data['purchase_price'])
        audit(req, 'update' if row else 'create', 'items', item_id)
        flash(req, T(req, 'saved'), 'success')
        return redirect(url_for('items'), req)
    if not row:
        row = {'active': 1, 'purchase_price': 0, 'sale_price': 0, 'reorder_level': 0}
    return render(req, 'item_form.html', title=T(req, 'edit_item' if rid else 'new_item'),
                  active='items', row=row, units=_units(req, rid) if rid else [],
                  categories=[r['category'] for r in q(req.db, "SELECT DISTINCT category FROM items "
                                                              "WHERE category!='' ORDER BY category")],
                  stores=core.scoped_stores(req))


@router.route('item_delete', '/items/<rid>/delete', ('POST',))
def item_delete(req, rid):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    ex(req.db, 'UPDATE items SET active=0 WHERE id=?', (rid,))
    audit(req, 'update', 'items', rid, 'deactivated')
    flash(req, T(req, 'items_deactivated'), 'warning')
    return redirect(url_for('items'), req)


@router.route('item_adjust', '/items/<rid>/adjust', ('POST',))
def item_adjust(req, rid):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    item = _item_row(req, rid)
    if not item:
        return core.not_found()
    store_id = req.i('store_id') or core.default_store(req)
    qty = core.safe_float(req.f('qty'))
    direction = req.f('direction') or 'in'
    if qty <= 0:
        flash(req, T(req, 'required_field'), 'danger')
        return redirect(url_for('item_edit', rid=rid), req)
    movement(req.db, rid, 0, store_id, qty if direction == 'in' else -qty, 'adjust', rid,
             (req.f('note') or '').strip() or T(req, 'stock_adjust'), today(), req.user['id'])
    audit(req, 'update', 'items', rid, f'adjust {direction} {qty}')
    flash(req, T(req, 'saved'), 'success')
    return redirect(url_for('item_edit', rid=rid), req)


@router.route('item_export', '/items/export', ('GET',))
def item_export(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    rows = q(req.db, '''SELECT i.code, i.name, i.name_en, i.category, i.brand, i.purchase_price,
                               i.sale_price, i.reorder_level,
                               COALESCE((SELECT SUM(m.qty) FROM stock_movements m WHERE m.item_id=i.id),0) stock,
                               (SELECT MIN(b.expiry_date) FROM item_batches b WHERE b.item_id=i.id
                                AND b.qty>0 AND b.expiry_date!='') expiry
                        FROM items i WHERE i.active=1 ORDER BY i.name''')
    header = [T(req, 'code'), T(req, 'item_name'), T(req, 'item_name_en'), T(req, 'category'),
              T(req, 'brand'), T(req, 'purchase_price'), T(req, 'sale_price'), T(req, 'reorder_level'),
              T(req, 'stock'), T(req, 'expiry')]
    data = [header] + [[r['code'], r['name'], r['name_en'], r['category'], r['brand'],
                        r['purchase_price'], r['sale_price'], r['reorder_level'], r['stock'],
                        r['expiry'] or ''] for r in rows]
    return export_rows('items.xlsx', data, sheet='items', money_cols=[6, 7])


@router.route('item_template', '/items/template', ('GET',))
def item_template(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    data = [[T(req, 'code'), T(req, 'item_name'), T(req, 'category'), T(req, 'purchase_price'),
             T(req, 'sale_price'), T(req, 'reorder_level'), T(req, 'barcode'), T(req, 'stock')],
            ['P-100', 'Sample item', 'Category', 10, 15, 5, '6281000000017', 20]]
    return export_rows('items-template.xlsx', data, sheet='template')


@router.route('item_import', '/items/import', ('GET', 'POST'))
def item_import(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    imported, errors = 0, []
    if req.method == 'POST':
        upload = req.files.get('file')
        if not upload or not upload.get('data'):
            flash(req, T(req, 'choose_file'), 'danger')
        else:
            try:
                rows = xlsx.sniff_rows(upload['filename'], upload['data'])
            except Exception as exc:
                rows = []
                errors.append(str(exc)[:160])
            update_existing = 1 if req.f('update_existing') else 0
            store_id = core.default_store(req)
            for index, row in enumerate(rows[1:], start=2):
                if not row or len(row) < 2 or str(row[1] or '').strip() == '':
                    continue
                try:
                    code = str(row[0] or '').strip()
                    name = str(row[1] or '').strip()
                    category = str(row[2] or '').strip() if len(row) > 2 else ''
                    purchase = core.safe_float(row[3] if len(row) > 3 else 0)
                    sale = core.safe_float(row[4] if len(row) > 4 else 0)
                    reorder = core.safe_float(row[5] if len(row) > 5 else 0)
                    barcode = str(row[6] or '').strip() if len(row) > 6 else ''
                    opening = core.safe_float(row[7] if len(row) > 7 else 0)
                except Exception:
                    errors.append(f'row {index}')
                    continue
                existing = one(req.db, 'SELECT id FROM items WHERE code=? AND code!=""', (code,)) if code else None
                if existing and update_existing:
                    ex(req.db, 'UPDATE items SET name=?, category=?, purchase_price=?, sale_price=?, '
                               'reorder_level=? WHERE id=?',
                       (name, category, purchase, sale, reorder, existing['id']))
                    item_id = existing['id']
                elif existing:
                    continue
                else:
                    item_id = x(req.db, 'INSERT INTO items(code,name,category,purchase_price,sale_price,'
                                       'reorder_level,active) VALUES(?,?,?,?,?,?,1)',
                                (code, name, category, purchase, sale, reorder))
                if not q(req.db, 'SELECT id FROM item_units WHERE item_id=?', (item_id,)):
                    x(req.db, 'INSERT INTO item_units(item_id,name,factor,barcode,sale_price,purchase_price,'
                             'is_base) VALUES(?,?,?,?,?,?,1)',
                      (item_id, T(req, 'piece'), 1, barcode or code, sale, purchase))
                if opening and store_id:
                    movement(req.db, item_id, 0, store_id, opening, 'opening', item_id,
                             T(req, 'import_items'), today(), req.user['id'])
                imported += 1
            if imported:
                audit(req, 'create', 'items', 0, f'import {imported}')
                flash(req, T(req, 'import_done', n=imported), 'success')
            elif not errors:
                flash(req, T(req, 'import_empty'), 'warning')
            for message in errors[:5]:
                flash(req, T(req, 'error') + ': ' + message, 'danger')
            return redirect(url_for('items'), req)
    return render(req, 'import.html', title=T(req, 'import_items'), active='item_import')


@router.route('items_bulk_price', '/items/prices', ('GET', 'POST'))
def items_bulk_price(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    if req.method == 'POST':
        ids = req.flist('item_id')
        sales = req.flist('sale_price')
        purchases = req.flist('purchase_price')
        changed = 0
        for index, item_id in enumerate(ids):
            if not str(item_id).isdigit():
                continue
            sale = core.safe_float(sales[index]) if index < len(sales) else None
            purchase = core.safe_float(purchases[index]) if index < len(purchases) else None
            item = one(req.db, 'SELECT * FROM items WHERE id=?', (item_id,))
            if not item:
                continue
            new_sale = sale if sale is not None else item['sale_price']
            new_purchase = purchase if purchase is not None else item['purchase_price']
            if abs(new_sale - core.safe_float(item['sale_price'])) > 1e-9 or \
                    abs(new_purchase - core.safe_float(item['purchase_price'])) > 1e-9:
                ex(req.db, 'UPDATE items SET sale_price=?, purchase_price=? WHERE id=?',
                   (new_sale, new_purchase, item_id))
                changed += 1
        audit(req, 'update', 'items', 0, f'bulk price {changed}')
        flash(req, T(req, 'prices_updated', n=changed), 'success')
        return redirect(url_for('items_bulk_price'), req)
    rows = q(req.db, '''SELECT i.*, (SELECT COUNT(*) FROM item_units u WHERE u.item_id=i.id) units
                        FROM items i WHERE i.active=1 ORDER BY i.name''')
    return render(req, 'bulk_price.html', title=T(req, 'bulk_price'), active='items', rows=rows)


@router.route('item_units_json', '/api/item/<iid>/units', ('GET',))
def item_units_json(req, iid):
    if not req.user:
        return core.json_error('unauthorized', '401 Unauthorized')
    rows = q(req.db, 'SELECT * FROM item_units WHERE item_id=? ORDER BY factor DESC', (iid,))
    return core.json_ok({'units': rows})


# --------------------------------------------------------------------------- #
# barcode labels
# --------------------------------------------------------------------------- #
@router.route('barcode', '/barcode', ('GET', 'POST'))
def barcode(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    if req.method == 'POST':
        ids = [value for value in req.flist('ids') if str(value).isdigit()]
        if not ids:
            flash(req, T(req, 'select_items'), 'danger')
            return redirect(url_for('barcode'), req)
        count = max(1, min(core.safe_int(req.f('count'), 1), 100))
        labels = []
        for item_id in ids:
            item = one(req.db, 'SELECT * FROM items WHERE id=?', (item_id,))
            if not item:
                continue
            units = _units(req, item_id)
            unit = units[0] if units else {}
            labels.append({'name': item['name'], 'code': unit.get('barcode') or item['code'],
                           'price': unit.get('sale_price') or item['sale_price'],
                           'barcode': unit.get('barcode') or item['code'], 'count': count})
        return render(req, 'barcode_print.html', title=T(req, 'barcode_labels'), active='barcode',
                      labels=labels, size=req.f('size') or 'small')
    rows = q(req.db, '''SELECT i.id, i.code, i.name, i.sale_price, i.category,
                               (SELECT COUNT(*) FROM item_units u WHERE u.item_id=i.id) units
                        FROM items i WHERE i.active=1 ORDER BY i.name LIMIT 1000''')
    return render(req, 'barcode.html', title=T(req, 'barcode_labels'), active='barcode',
                  subtitle=T(req, 'barcode_labels_sub'), rows=rows)


# --------------------------------------------------------------------------- #
# store transfers
# --------------------------------------------------------------------------- #
@router.route('transfers', '/transfers', ('GET',))
def transfers(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    where, args = [], []
    if req.q('d1'):
        where.append('t.tdate>=?')
        args.append(req.q('d1'))
    if req.q('d2'):
        where.append('t.tdate<=?')
        args.append(req.q('d2'))
    clause = 'WHERE ' + ' AND '.join(where) if where else ''
    rows = q(req.db, f'''SELECT t.*, s1.name from_name, s2.name to_name,
                                (SELECT COUNT(*) FROM transfer_items i WHERE i.transfer_id=t.id) lines,
                                (SELECT COALESCE(SUM(i.qty),0) FROM transfer_items i
                                 WHERE i.transfer_id=t.id) qty
                         FROM transfers t JOIN stores s1 ON s1.id=t.from_store
                         JOIN stores s2 ON s2.id=t.to_store {clause}
                         ORDER BY t.id DESC LIMIT 300''', args)
    return render(req, 'transfer_list.html', title=T(req, 'store_transfers'), active='transfers',
                  rows=rows)


@router.route('transfer_new', '/transfers/new', ('GET', 'POST'))
def transfer_new(req):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    stores = core.scoped_stores(req)
    items_list = q(req.db, '''SELECT i.id, i.code, i.name,
                                     COALESCE((SELECT SUM(m.qty) FROM stock_movements m
                                               WHERE m.item_id=i.id),0) stock
                              FROM items i WHERE i.active=1 ORDER BY i.name''')
    if req.method == 'POST':
        from_store = req.i('from_store') or (stores[0]['id'] if stores else 0)
        to_store = req.i('to_store')
        if from_store == to_store:
            flash(req, T(req, 'transfer_same_store'), 'danger')
            return redirect(url_for('transfer_new'), req)
        pairs = list(zip(req.flist('item_id'), req.flist('unit_id'), req.flist('qty')))
        lines = []
        for item_id, unit_id, qty_raw in pairs:
            if not item_id or str(qty_raw).strip() == '':
                continue
            qty = core.safe_float(qty_raw)
            if qty <= 0:
                continue
            unit_id = unit_id or (one(req.db, 'SELECT id FROM item_units WHERE item_id=? ORDER BY factor LIMIT 1',
                                      (item_id,)) or {}).get('id') or 0
            lines.append((int(item_id), int(unit_id), qty))
        if not lines:
            flash(req, T(req, 'add_at_least_one_line'), 'danger')
            return redirect(url_for('transfer_new'), req)
        totals = {}
        for item_id, unit_id, qty in lines:
            totals[item_id] = totals.get(item_id, 0) + core.base_qty(req.db, unit_id, qty)
        for item_id, total in totals.items():
            if core.stock_of(req.db, item_id, from_store) + 1e-9 < total:
                flash(req, T(req, 'not_enough_stock', name=core.item_name(req.db, item_id)), 'danger')
                return redirect(url_for('transfer_new'), req)
        count = scalar(req.db, 'SELECT COUNT(*) c FROM transfers') + 1
        number = f'TR-{count:05d}'
        transfer_id = x(req.db, 'INSERT INTO transfers(number,from_store,to_store,tdate,note,user_id,ctime) '
                                'VALUES(?,?,?,?,?,?,?)',
                        (number, from_store, to_store, req.f('tdate') or today(),
                         (req.f('note') or '').strip(), req.user['id'], time.time()))
        for item_id, unit_id, qty in lines:
            base = core.base_qty(req.db, unit_id, qty)
            x(req.db, 'INSERT INTO transfer_items(transfer_id,item_id,unit_id,qty) VALUES(?,?,?,?)',
              (transfer_id, item_id, unit_id, qty))
            movement(req.db, item_id, unit_id, from_store, -base, 'transfer', transfer_id, number,
                     req.f('tdate') or today(), req.user['id'])
            movement(req.db, item_id, unit_id, to_store, base, 'transfer', transfer_id, number,
                     req.f('tdate') or today(), req.user['id'])
        audit(req, 'create', 'transfers', transfer_id, number)
        flash(req, T(req, 'transfer_saved'), 'success')
        return redirect(url_for('transfer_print', tid=transfer_id), req)
    return render(req, 'transfer_form.html', title=T(req, 'new_transfer'), active='transfers',
                  stores=stores, items_list=items_list,
                  units=q(req.db, 'SELECT id, item_id, name, factor FROM item_units ORDER BY factor DESC'))


@router.route('transfer_print', '/transfers/<tid>/print', ('GET',))
def transfer_print(req, tid):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    transfer = one(req.db, 'SELECT * FROM transfers WHERE id=?', (tid,))
    if not transfer:
        return core.not_found()
    lines = q(req.db, '''SELECT ti.*, i.name item_name, i.code item_code, u.name unit_name
                         FROM transfer_items ti JOIN items i ON i.id=ti.item_id
                         LEFT JOIN item_units u ON u.id=ti.unit_id WHERE ti.transfer_id=?''', (tid,))
    from_store = one(req.db, 'SELECT * FROM stores WHERE id=?', (transfer['from_store'],)) or {}
    to_store = one(req.db, 'SELECT * FROM stores WHERE id=?', (transfer['to_store'],)) or {}
    return render(req, 'transfer_print.html', title=T(req, 'transfer_print'), active='transfers',
                  transfer=transfer, lines=lines, from_store=from_store, to_store=to_store)


@router.route('transfer_delete', '/transfers/<tid>/delete', ('POST',))
def transfer_delete(req, tid):
    blocked = guard(req, 'p_inv')
    if blocked:
        return blocked
    if one(req.db, 'SELECT id FROM transfers WHERE id=?', (tid,)):
        ex(req.db, "DELETE FROM stock_movements WHERE ref_type='transfer' AND ref_id=?", (tid,))
        ex(req.db, 'DELETE FROM transfer_items WHERE transfer_id=?', (tid,))
        ex(req.db, 'DELETE FROM transfers WHERE id=?', (tid,))
        audit(req, 'delete', 'transfers', tid)
        flash(req, T(req, 'deleted'), 'warning')
    return redirect(url_for('transfers'), req)
