# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - generic CRUD engine + simple master-data screens.

Everything that is a straightforward record list (stores, parties, tiers,
vouchers-adjacent records, HR tables, accounts…) is declared here as data.
Document screens with real business logic live in the other view modules.
"""
import os
import time

import core
import db
import i18n
import xlsx
from core import (audit, base_qty, can, export_rows, flash, guard, money, next_number,
                  num, party_delta, q, render, one, today, x, T)
from db import ex, scalar
from framework import hash_password, read_upload, redirect, router, url_for

CRUDS = {}


def sel(req, sql, args=()):
    return [(row['id'], row['name']) for row in q(req.db, sql, args)]


def lookup(req, table, row_id, column='name'):
    if not row_id:
        return ''
    row = one(req.db, f'SELECT {column} c FROM {table} WHERE id=?', (row_id,))
    return row['c'] if row else ''


def yes_no(req, value):
    return T(req, 'yes') if value else '—'


class Crud:
    """Declarative list/create/edit/delete screens."""

    def __init__(self, key, table, title, fields, list_cols, perm='p_settings', subtitle='',
                 search=None, order='id DESC', can_delete=True, can_add=True, can_edit=True,
                 form_hint='', scope=None, after_save=None, after_delete=None, before_save=None,
                 row_actions=None, upsert=None, per_page=50, exportable=True, icon='layers',
                 filters=None, back=None, delete_guard=None, before_update=None):
        self.key = key
        self.table = table
        self.title = title
        self.subtitle = subtitle
        self.fields = fields
        self.list_cols = list_cols
        self.perm = perm
        self.search = search or []
        self.order = order
        self.can_delete = can_delete
        self.can_add = can_add
        self.can_edit = can_edit
        self.form_hint = form_hint
        self.scope = scope
        self.after_save = after_save
        self.after_delete = after_delete
        self.before_save = before_save
        self.row_actions = row_actions or []
        self.upsert = upsert or []
        self.per_page = per_page
        self.exportable = exportable
        self.icon = icon
        self.filters = filters or []
        self.back = back or key
        self.delete_guard = delete_guard
        self.before_update = before_update
        CRUDS[key] = self
        self._register()

    # ---- internals ------------------------------------------------------ #
    def _register(self):
        key = self.key

        @router.route(key, f'/{key}', ('GET',))
        def _list(req):
            return self.list_view(req)

        if self.can_add:
            @router.route(key + '_new', f'/{key}/new', ('GET', 'POST'))
            def _new(req):
                return self.form_view(req)

        if self.can_edit:
            @router.route(key + '_edit', f'/{key}/<rid>/edit', ('GET', 'POST'))
            def _edit(req, rid):
                row = one(req.db, f'SELECT * FROM {self.table} WHERE id=?', (rid,))
                if not row:
                    return core.not_found()
                return self.form_view(req, row)

        if self.can_delete:
            @router.route(key + '_del', f'/{key}/<rid>/delete', ('POST',))
            def _del(req, rid):
                return self.delete_view(req, rid)

        if self.exportable:
            @router.route(key + '_export', f'/{key}/export', ('GET',))
            def _export(req):
                return self.export_view(req)

    def options(self, req, field):
        opts = field.get('options')
        if callable(opts):
            return opts(req)
        result = []
        for value, label in opts or []:
            result.append((value, T(req, label) if i18n.has(label) else label))
        return result

    def where_clause(self, req):
        where, args = ['1=1'], []
        search = (req.q('s') or '').strip()
        if search and self.search:
            where.append('(' + ' OR '.join(f'{col} LIKE ?' for col in self.search) + ')')
            args += [f'%{search}%'] * len(self.search)
        for param, _label, expr, _opts in self.filters:
            value = req.q(param)
            if value not in (None, ''):
                if callable(expr):
                    expr, extra = expr(req, value)
                    where.append(expr)
                    args += list(extra)
                else:
                    where.append(expr)
                    args.append(value)
        if self.scope == 'store' and req.company.get('restrict_scope') and (req.user or {}).get('store_id') \
                and not (req.user or {}).get('is_admin'):
            where.append('store_id=?')
            args.append(req.user['store_id'])
        return ' AND '.join(where), args

    # ---- list ----------------------------------------------------------- #
    def list_view(self, req):
        blocked = guard(req, self.perm)
        if blocked:
            return blocked
        where, args = self.where_clause(req)
        page, per, offset = core.page_params(req, self.per_page)
        total = scalar(req.db, f'SELECT COUNT(*) c FROM {self.table} WHERE {where}', args)
        rows = q(req.db, f'SELECT * FROM {self.table} WHERE {where} ORDER BY {self.order} '
                         f'LIMIT ? OFFSET ?', args + [per, offset])
        for row in rows:
            for col in self.list_cols:
                fmt = col.get('fmt')
                try:
                    row[col['key'] + '_v'] = fmt(req, row) if fmt else row.get(col['key'], '')
                except Exception:
                    row[col['key'] + '_v'] = row.get(col['key'], '')
        filter_values = {param: req.q(param) or '' for param, *_ in self.filters}
        return render(req, 'crud_list.html', title=T(req, self.title),
                      subtitle=T(req, self.subtitle) if self.subtitle else '',
                      crud=self, rows=rows, total=total,
                      pager=core.pager(req, total, page, per), search=req.q('s') or '',
                      filter_values=filter_values, active=self.key)

    def export_view(self, req):
        blocked = guard(req, self.perm)
        if blocked:
            return blocked
        where, args = self.where_clause(req)
        rows = q(req.db, f'SELECT * FROM {self.table} WHERE {where} ORDER BY {self.order}', args)
        header = [T(req, 'number')] + [T(req, col['label']) for col in self.list_cols]
        data = [header]
        for idx, row in enumerate(rows, start=1):
            line = [idx]
            for col in self.list_cols:
                value = row.get(col['key'])
                fmt = col.get('fmt')
                if fmt:
                    try:
                        value = fmt(req, row)
                    except Exception:
                        pass
                line.append(value if value is not None else '')
            data.append(line)
        return export_rows(f'{self.key}.xlsx', data, sheet=self.key)

    # ---- form ----------------------------------------------------------- #
    def _collect(self, req, row):
        data, errors = {}, []
        for field in self.fields:
            name = field['name']
            ftype = field.get('type', 'text')
            if field.get('fixed') is not None:
                data[name] = field['fixed']
                continue
            if ftype == 'checkbox':
                data[name] = 1 if req.f(name) else 0
            elif ftype == 'money':
                data[name] = core.safe_float(req.f(name))
            elif ftype == 'number':
                data[name] = core.safe_float(req.f(name))
            elif ftype == 'password':
                value = req.f(name)
                if value:
                    data[name] = hash_password(value)
                elif row:
                    data[name] = row.get(name, '')
                else:
                    data[name] = hash_password('123456')
            elif ftype == 'file':
                stored = read_upload(req.files.get(name), db.UPLOAD_DIR, f'{self.table}')
                if stored:
                    data[name] = stored
                elif row:
                    data[name] = row.get(name, '')
                else:
                    data[name] = ''
            else:
                data[name] = (req.f(name) or '').strip()
            if field.get('required') and ftype != 'password':
                if data.get(name) in ('', None, 0) and ftype != 'checkbox':
                    errors.append(T(req, 'required_field') + ': ' + T(req, field['label']))
            if field.get('type') == 'date' and data.get(name) == '':
                data[name] = today() if field.get('default_today') else ''
            if field.get('default') not in (None, '') and data.get(name) in ('', None) and not row:
                data[name] = field['default']
        if self.before_save:
            self.before_save(req, data, row)
        return data, errors

    def form_view(self, req, row=None):
        blocked = guard(req, self.perm)
        if blocked:
            return blocked
        errors = []
        if req.method == 'POST':
            data, errors = self._collect(req, row)
            if not errors:
                if row:
                    if self.before_update:
                        self.before_update(req, row)
                    sets = ', '.join(f'{k}=?' for k in data)
                    req.db.execute(f'UPDATE {self.table} SET {sets} WHERE id=?',
                                   tuple(data.values()) + (row['id'],))
                    req.db.commit()
                    rid = row['id']
                else:
                    if self.upsert:
                        clause = ' AND '.join(f'{k}=?' for k in self.upsert)
                        existing = one(req.db, f'SELECT id FROM {self.table} WHERE {clause}',
                                       tuple(data.get(k, '') for k in self.upsert))
                        if existing:
                            sets = ', '.join(f'{k}=?' for k in data)
                            req.db.execute(f'UPDATE {self.table} SET {sets} WHERE id=?',
                                           tuple(data.values()) + (existing['id'],))
                            req.db.commit()
                            rid = existing['id']
                        else:
                            rid = self._insert(req, data)
                    else:
                        rid = self._insert(req, data)
                if self.after_save:
                    self.after_save(req, rid, data)
                audit(req, 'create' if not row else 'update', self.table, rid)
                flash(req, T(req, 'saved'), 'success')
                if req.f('_and_new'):
                    return redirect(url_for(self.key + '_new'), req)
                return redirect(url_for(self.back) if self.back in router.names() else url_for(self.key), req)
            filled = dict(data)
            filled['id'] = row['id'] if row else 0
            row = filled
        return render(req, 'crud_form.html', title=T(req, self.title),
                      subtitle=T(req, self.subtitle) if self.subtitle else '',
                      crud=self, row=row or {}, errors=errors, active=self.key,
                      is_new=not (row or {}).get('id'))

    def _insert(self, req, data):
        cols = ', '.join(data.keys())
        placeholders = ','.join('?' * len(data))
        return x(req.db, f'INSERT INTO {self.table}({cols}) VALUES({placeholders})',
                 tuple(data.values()))

    def delete_view(self, req, rid):
        blocked = guard(req, self.perm)
        if blocked:
            return blocked
        row = one(req.db, f'SELECT * FROM {self.table} WHERE id=?', (rid,))
        if not row:
            return core.not_found()
        if self.delete_guard:
            message = self.delete_guard(req, row)
            if message:
                flash(req, message, 'danger')
                target = self.back if self.back in router.names() else self.key
                return redirect(url_for(target), req)
        if self.after_delete:
            self.after_delete(req, row)
        ex(req.db, f'DELETE FROM {self.table} WHERE id=?', (rid,))
        audit(req, 'delete', self.table, rid)
        flash(req, T(req, 'deleted'), 'warning')
        target = self.back if self.back in router.names() else self.key
        return redirect(url_for(target), req)


# --------------------------------------------------------------------------- #
# formatting helpers used by list columns
# --------------------------------------------------------------------------- #
def f_money(key):
    def fmt(req, row):
        return core.money(row.get(key))
    return fmt


def f_bool(key):
    def fmt(req, row):
        return T(req, 'yes') if row.get(key) else '—'
    return fmt


def f_lookup(table, key, column='name'):
    def fmt(req, row):
        return lookup(req, table, row.get(key), column)
    return fmt


def f_map(key, mapping):
    def fmt(req, row):
        value = row.get(key)
        return T(req, mapping[value]) if value in mapping else '—'
    return fmt


def f_active(key='active'):
    def fmt(req, row):
        return T(req, 'active') if row.get(key) else T(req, 'pending_approval')
    return fmt


def f_date(key='date'):
    def fmt(req, row):
        return row.get(key) or '—'
    return fmt


F = lambda **kw: kw
L = lambda key, label, **kw: dict(key=key, label=label, **kw)


def _movement(req, row, sign=-1, note=''):
    qty = core.base_qty(req.db, row['unit_id'], row['qty'])
    core.movement(req.db, row['item_id'], row['unit_id'], row['store_id'], sign * qty,
                  'damage', row['id'], note or row.get('note', ''), row.get('ddate'), req.user['id'])


def damage_saved(req, rid, data):
    row = one(req.db, 'SELECT * FROM damages WHERE id=?', (rid,))
    if row:
        _movement(req, row)


def damage_deleted(req, row):
    ex(req.db, "DELETE FROM stock_movements WHERE ref_type='damage' AND ref_id=?", (row['id'],))


def withdrawal_saved(req, rid, data):
    core.treasury_delta(req.db, data['treasury_id'], -core.safe_float(data.get('amount')))


def withdrawal_edited(req, rid, data):
    withdrawal_saved(req, rid, data)


def withdrawal_deleted(req, row):
    core.treasury_delta(req.db, row['treasury_id'], core.safe_float(row['amount']))


def credit_note_sign(party_type, ctype):
    """A credit note reduces the party balance; a debit note increases it."""
    return -1 if ctype == 'discount' else 1


def credit_note_saved(req, rid, data):
    core.party_delta(req.db, data['party_type'], data['party_id'],
                     credit_note_sign(data['party_type'], data['ctype']) * core.safe_float(data['amount']))


def credit_note_deleted(req, row):
    core.party_delta(req.db, row['party_type'], row['party_id'],
                     -credit_note_sign(row['party_type'], row['ctype']) * core.safe_float(row['amount']))


def transfer_saved(req, rid, data):
    amount = core.safe_float(data['amount'])
    core.treasury_delta(req.db, data['tfrom'], -amount)
    core.treasury_delta(req.db, data['tto'], amount)


def transfer_deleted(req, row):
    amount = core.safe_float(row['amount'])
    core.treasury_delta(req.db, row['tfrom'], amount)
    core.treasury_delta(req.db, row['tto'], -amount)


def withdrawal_before_update(req, row):
    core.treasury_delta(req.db, row['treasury_id'], core.safe_float(row['amount']))


def damage_before_update(req, row):
    ex(req.db, "DELETE FROM stock_movements WHERE ref_type='damage' AND ref_id=?", (row['id'],))


def transfer_before_update(req, row):
    amount = core.safe_float(row['amount'])
    core.treasury_delta(req.db, row['tfrom'], amount)
    core.treasury_delta(req.db, row['tto'], -amount)


def party_before_save(req, data, row):
    opening = core.safe_float(data.get('opening_balance'))
    if row:
        diff = opening - core.safe_float(row.get('opening_balance'))
        if diff:
            data['balance'] = core.safe_float(row.get('balance')) + diff
    else:
        data['balance'] = opening


def tier_before_save(req, data, row):
    if data.get('is_default'):
        ex(req.db, 'UPDATE price_tiers SET is_default=0')


def deny_admin_user_delete(req, row):
    if row['id'] == (req.user or {}).get('id'):
        return T(req, 'cannot_delete_self')
    if row.get('username') == 'admin':
        return T(req, 'cannot_delete_admin')
    return None


def users_before_save(req, data, row):
    """New accounts start their 14-day trial at creation, not at first login."""
    if row:
        return
    now = time.time()
    data['created_at'] = now
    if not data.get('is_admin'):
        data['trial_started'] = now
        if not data.get('role_id'):
            data['role_id'] = db.default_role_id(req.db)


def deny_system_role_delete(req, row):
    if row.get('is_system'):
        return T(req, 'cannot_delete_system_role')
    return None


def deny_store_delete(req, row):
    used = scalar(req.db, 'SELECT COUNT(*) c FROM stock_movements WHERE store_id=?', (row['id'],))
    if used:
        return T(req, 'cannot_delete_in_use')
    return None


def deny_treasury_delete(req, row):
    used = scalar(req.db, 'SELECT COUNT(*) c FROM vouchers WHERE treasury_id=?', (row['id'],))
    if used:
        return T(req, 'cannot_delete_in_use')
    return None


def deny_account_delete(req, row):
    used = scalar(req.db, 'SELECT COUNT(*) c FROM journal_lines WHERE account_id=?', (row['id'],))
    if used:
        return T(req, 'cannot_delete_in_use')
    return None


# --------------------------------------------------------------------------- #
# master data
# --------------------------------------------------------------------------- #
Crud('stores', 'stores', 'stores', [
    F(name='name', label='name', type='text', required=1),
    F(name='location', label='location', type='text'),
    F(name='phone', label='phone', type='text'),
    F(name='manager', label='manager', type='text'),
    F(name='active', label='active', type='checkbox', default=1)],
    [L('name', 'name'), L('location', 'location'), L('manager', 'manager'), L('active', 'active', fmt=f_bool('active'))],
    perm='p_inv', subtitle='stores_sub', search=['name', 'location'], icon='store',
    delete_guard=deny_store_delete)

Crud('treasuries', 'treasuries', 'treasuries', [
    F(name='name', label='name', type='text', required=1),
    F(name='ttype', label='type', type='select', options=[('cash', 'treasury_cash'), ('bank', 'treasury_bank')]),
    F(name='balance', label='opening_balance', type='number', default=0),
    F(name='account_no', label='account_no', type='text'),
    F(name='iban', label='iban', type='text'),
    F(name='bank_name', label='bank_name', type='text'),
    F(name='active', label='active', type='checkbox', default=1)],
    [L('name', 'name'), L('ttype', 'type', fmt=f_map('ttype', {'cash': 'treasury_cash', 'bank': 'treasury_bank'})),
     L('balance', 'balance', fmt=f_money('balance'), align='end'), L('active', 'active', fmt=f_bool('active'))],
    perm='p_treasury', subtitle='treasuries_sub', search=['name'], icon='wallet',
    delete_guard=deny_treasury_delete)

Crud('price_tiers', 'price_tiers', 'price_tiers', [
    F(name='name', label='tier_name', type='text', required=1),
    F(name='percent', label='tier_percent', type='number', step='0.01', default=0),
    F(name='is_default', label='default_tier', type='checkbox'),
    F(name='notes', label='notes', type='textarea')],
    [L('name', 'tier_name'), L('percent', 'tier_percent', fmt=lambda r, row: core.pct(row.get('percent')), align='end'),
     L('is_default', 'default_tier', fmt=f_bool('is_default'))],
    perm='p_sell', subtitle='tier_hint', search=['name'], icon='tag', before_save=tier_before_save)

Crud('customers', 'customers', 'customers', [
    F(name='name', label='customer_name', type='text', required=1),
    F(name='phone', label='phone', type='text'),
    F(name='email', label='email', type='text'),
    F(name='address', label='address', type='text'),
    F(name='tax_number', label='tax_number', type='text'),
    F(name='tier_id', label='price_tier', type='select', options=lambda req: sel(req, 'SELECT id,name FROM price_tiers ORDER BY id')),
    F(name='credit_limit', label='credit_limit', type='number', default=0),
    F(name='opening_balance', label='opening_balance', type='number', default=0),
    F(name='active', label='active', type='checkbox', default=1),
    F(name='notes', label='notes', type='textarea')],
    [L('name', 'customer_name'), L('phone', 'phone'), L('tier_id', 'price_tier', fmt=f_lookup('price_tiers', 'tier_id')),
     L('balance', 'balance', fmt=f_money('balance'), align='end'),
     L('credit_limit', 'credit_limit', fmt=f_money('credit_limit'), align='end'),
     L('active', 'active', fmt=f_bool('active'))],
    perm='p_sell', subtitle='customers_sub', search=['name', 'phone', 'email'], icon='users',
    before_save=party_before_save)

Crud('suppliers', 'suppliers', 'suppliers', [
    F(name='name', label='supplier_name', type='text', required=1),
    F(name='phone', label='phone', type='text'),
    F(name='email', label='email', type='text'),
    F(name='address', label='address', type='text'),
    F(name='tax_number', label='tax_number', type='text'),
    F(name='opening_balance', label='opening_balance', type='number', default=0),
    F(name='active', label='active', type='checkbox', default=1),
    F(name='notes', label='notes', type='textarea')],
    [L('name', 'supplier_name'), L('phone', 'phone'), L('balance', 'balance', fmt=f_money('balance'), align='end'),
     L('active', 'active', fmt=f_bool('active'))],
    perm='p_buy', subtitle='suppliers_sub', search=['name', 'phone'], icon='briefcase',
    before_save=party_before_save)

Crud('offers', 'offers', 'offers', [
    F(name='name', label='offer_name', type='text', required=1),
    F(name='scope', label='offer_scope', type='select', options=[('item', 'scope_item'), ('all', 'scope_all')]),
    F(name='item_id', label='item', type='select', options=lambda req: sel(req, 'SELECT id,name FROM items WHERE active=1 ORDER BY name')),
    F(name='discount_percent', label='offer_percent', type='number', step='0.01', required=1),
    F(name='start_date', label='start_date', type='date', default_today=1),
    F(name='end_date', label='end_date', type='date', default_today=1),
    F(name='active', label='active', type='checkbox', default=1)],
    [L('name', 'offer_name'), L('scope', 'offer_scope', fmt=f_map('scope', {'item': 'scope_item', 'all': 'scope_all'})),
     L('item_id', 'item', fmt=f_lookup('items', 'item_id')),
     L('discount_percent', 'offer_percent', fmt=lambda r, row: core.pct(row.get('discount_percent')), align='end'),
     L('start_date', 'start_date'), L('end_date', 'end_date'), L('active', 'active', fmt=f_bool('active'))],
    perm='p_inv', subtitle='offers_sub', search=['name'], icon='percent')

# --------------------------------------------------------------------------- #
# stock documents
# --------------------------------------------------------------------------- #
Crud('damages', 'damages', 'damages', [
    F(name='item_id', label='item', type='select', required=1,
      options=lambda req: sel(req, "SELECT id, name||' · '||code name FROM items WHERE active=1 ORDER BY name")),
    F(name='store_id', label='store', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM stores ORDER BY name')),
    F(name='qty', label='qty', type='number', step='any', required=1),
    F(name='unit_id', label='unit', type='select',
      options=lambda req: sel(req, 'SELECT u.id, u.name||\' · \'||i.name name FROM item_units u JOIN items i ON i.id=u.item_id ORDER BY i.name')),
    F(name='reason', label='damage_reason', type='select',
      options=[('expired', 'reason_expired'), ('broken', 'reason_broken'), ('lost', 'reason_lost'), ('other', 'reason_other')]),
    F(name='ddate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('item_id', 'item', fmt=f_lookup('items', 'item_id')), L('store_id', 'store', fmt=f_lookup('stores', 'store_id')),
     L('qty', 'qty', align='end'), L('reason', 'damage_reason',
                                   fmt=f_map('reason', {'expired': 'reason_expired', 'broken': 'reason_broken',
                                                        'lost': 'reason_lost', 'other': 'reason_other'})),
     L('ddate', 'date')],
    perm='p_inv', subtitle='damages_sub', icon='alert', search=['note'],
    after_save=damage_saved, after_delete=damage_deleted, before_update=damage_before_update)

Crud('withdrawals', 'withdrawals', 'withdrawals', [
    F(name='treasury_id', label='treasury', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM treasuries ORDER BY name')),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='wtype', label='withdrawal_type', type='select',
      options=[('personal', 'withdrawal_personal'), ('gift', 'withdrawal_gift')]),
    F(name='wdate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('treasury_id', 'treasury', fmt=f_lookup('treasuries', 'treasury_id')),
     L('amount', 'amount', fmt=f_money('amount'), align='end'),
     L('wtype', 'withdrawal_type', fmt=f_map('wtype', {'personal': 'withdrawal_personal', 'gift': 'withdrawal_gift'})),
     L('wdate', 'date')],
    perm='p_treasury', subtitle='withdrawals_sub', icon='arrow-down',
    after_save=withdrawal_saved, after_delete=withdrawal_deleted,
    before_update=withdrawal_before_update)

Crud('credit_notes', 'credit_notes', 'credit_note_customer', [
    F(name='party_type', fixed='customer', label='party_type', type='hidden'),
    F(name='number', fixed='', label='number', type='hidden'),
    F(name='party_id', label='customer', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM customers ORDER BY name')),
    F(name='ctype', label='credit_note_type', type='select',
      options=[('discount', 'note_discount_customer'), ('add', 'note_add_customer')]),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='cdate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('party_id', 'customer', fmt=f_lookup('customers', 'party_id')),
     L('ctype', 'credit_note_type', fmt=f_map('ctype', {'discount': 'note_discount', 'add': 'note_add'})),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('cdate', 'date')],
    perm='p_sell', subtitle='credit_notes_sub', icon='receipt',
    after_save=credit_note_saved, after_delete=credit_note_deleted,
    before_update=credit_note_deleted, order='cdate DESC, id DESC')

Crud('sup_credit_notes', 'credit_notes', 'credit_note_supplier', [
    F(name='party_type', fixed='supplier', label='party_type', type='hidden'),
    F(name='number', fixed='', label='number', type='hidden'),
    F(name='party_id', label='supplier', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM suppliers ORDER BY name')),
    F(name='ctype', label='credit_note_type', type='select',
      options=[('discount', 'note_discount_supplier'), ('add', 'note_add_supplier')]),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='cdate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('party_id', 'supplier', fmt=f_lookup('suppliers', 'party_id')),
     L('ctype', 'credit_note_type', fmt=f_map('ctype', {'discount': 'note_discount', 'add': 'note_add'})),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('cdate', 'date')],
    perm='p_buy', icon='receipt',
    after_save=credit_note_saved, after_delete=credit_note_deleted,
    before_update=credit_note_deleted, order='cdate DESC, id DESC')

Crud('treasury_transfers', 'treasury_transfers', 'treasury_transfers', [
    F(name='tfrom', label='from_treasury', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM treasuries ORDER BY name')),
    F(name='tto', label='to_treasury', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM treasuries ORDER BY name')),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='tdate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('tfrom', 'from_treasury', fmt=f_lookup('treasuries', 'tfrom')),
     L('tto', 'to_treasury', fmt=f_lookup('treasuries', 'tto')),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('tdate', 'date')],
    perm='p_treasury', subtitle='treasury_transfers_sub', icon='swap',
    after_save=transfer_saved, after_delete=transfer_deleted, before_update=transfer_before_update,
    order='tdate DESC, id DESC')

Crud('cheques', 'cheques', 'cheques', [
    F(name='number', label='cheque_number', type='text', required=1),
    F(name='ctype', label='cheque_type', type='select', options=[('in', 'cheque_in'), ('out', 'cheque_out')]),
    F(name='party_type', label='party_type', type='select', options=[('customer', 'party_customer'), ('supplier', 'party_supplier')]),
    F(name='party_id', label='party', type='select',
      options=lambda req: sel(req, "SELECT id, name FROM customers UNION ALL SELECT id, name FROM suppliers")),
    F(name='bank', label='cheque_bank', type='text'),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='due_date', label='due_date', type='date', default_today=1),
    F(name='treasury_id', label='treasury', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM treasuries ORDER BY name')),
    F(name='status', label='status', type='select',
      options=[('pending', 'cheque_pending'), ('cleared', 'cheque_cleared'), ('bounced', 'cheque_bounced')]),
    F(name='note', label='note', type='textarea')],
    [L('number', 'cheque_number'), L('ctype', 'cheque_type', fmt=f_map('ctype', {'in': 'cheque_in', 'out': 'cheque_out'})),
     L('party_id', 'party', fmt=f_lookup('customers', 'party_id')),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('due_date', 'due_date'),
     L('status', 'status', fmt=f_map('status', {'pending': 'cheque_pending', 'cleared': 'cheque_cleared',
                                                'bounced': 'cheque_bounced'}))],
    perm='p_treasury', subtitle='cheques_sub', search=['number', 'bank'], icon='credit-card',
    order='due_date ASC, id DESC')

Crud('reminders', 'reminders', 'reminders', [
    F(name='title', label='reminder_title', type='text', required=1),
    F(name='rdate', label='date', type='date', default_today=1),
    F(name='priority', label='priority', type='select',
      options=[('low', 'priority_low'), ('normal', 'priority_normal'), ('high', 'priority_high')]),
    F(name='note', label='notes', type='textarea'),
    F(name='done', label='reminder_done', type='checkbox')],
    [L('title', 'reminder_title'), L('rdate', 'date'),
     L('priority', 'priority', fmt=f_map('priority', {'low': 'priority_low', 'normal': 'priority_normal', 'high': 'priority_high'})),
     L('done', 'reminder_done', fmt=f_bool('done'))],
    subtitle='reminders_sub', search=['title', 'note'], icon='bell', order='done ASC, rdate ASC',
    perm=None)

# --------------------------------------------------------------------------- #
# human resources
# --------------------------------------------------------------------------- #
Crud('job_roles', 'job_roles', 'job_roles', [
    F(name='name', label='job_role', type='text', required=1),
    F(name='notes', label='notes', type='text')],
    [L('name', 'job_role'), L('notes', 'notes')],
    perm='p_hr', icon='briefcase', search=['name'])

Crud('salary_packages', 'salary_packages', 'salary_packages', [
    F(name='name', label='salary_package', type='text', required=1),
    F(name='basic', label='package_basic', type='number', step='any', default=0),
    F(name='housing', label='package_housing', type='number', step='any', default=0),
    F(name='transport', label='package_transport', type='number', step='any', default=0),
    F(name='allowance', label='package_allowance', type='number', step='any', default=0),
    F(name='notes', label='notes', type='text')],
    [L('name', 'salary_package'), L('basic', 'package_basic', fmt=f_money('basic'), align='end'),
     L('housing', 'package_housing', fmt=f_money('housing'), align='end'),
     L('transport', 'package_transport', fmt=f_money('transport'), align='end'),
     L('allowance', 'package_allowance', fmt=f_money('allowance'), align='end')],
    perm='p_hr', icon='wallet', search=['name'])

Crud('employees', 'employees', 'employees', [
    F(name='name', label='name', type='text', required=1),
    F(name='phone', label='phone', type='text'),
    F(name='email', label='email', type='text'),
    F(name='national_id', label='national_id', type='text'),
    F(name='job_role_id', label='job_role', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM job_roles ORDER BY name')),
    F(name='package_id', label='salary_package', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM salary_packages ORDER BY name')),
    F(name='salary_type', label='salary_type', type='select',
      options=[('monthly', 'salary_monthly'), ('daily', 'salary_daily')]),
    F(name='salary', label='salary', type='number', step='any', default=0),
    F(name='daily_rate', label='daily_rate', type='number', step='any', default=0),
    F(name='hire_date', label='hire_date', type='date'),
    F(name='active', label='active', type='checkbox', default=1),
    F(name='notes', label='notes', type='textarea')],
    [L('name', 'name'), L('phone', 'phone'), L('job_role_id', 'job_role', fmt=f_lookup('job_roles', 'job_role_id')),
     L('salary_type', 'salary_type', fmt=f_map('salary_type', {'monthly': 'salary_monthly', 'daily': 'salary_daily'})),
     L('salary', 'salary', fmt=f_money('salary'), align='end'),
     L('daily_rate', 'daily_rate', fmt=f_money('daily_rate'), align='end'),
     L('active', 'active', fmt=f_bool('active'))],
    perm='p_hr', subtitle='employees_sub', search=['name', 'phone'], icon='id')

Crud('attendance', 'attendance', 'attendance', [
    F(name='employee_id', label='employee', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM employees WHERE active=1 ORDER BY name')),
    F(name='adate', label='date', type='date', default_today=1),
    F(name='status', label='status', type='select',
      options=[('present', 'att_present'), ('absent', 'att_absent'), ('late', 'att_late')]),
    F(name='hours', label='att_hours', type='number', step='any', default=0),
    F(name='note', label='notes', type='text')],
    [L('employee_id', 'employee', fmt=f_lookup('employees', 'employee_id')),
     L('adate', 'date'),
     L('status', 'status', fmt=f_map('status', {'present': 'att_present', 'absent': 'att_absent', 'late': 'att_late'})),
     L('hours', 'att_hours', align='end')],
    perm='p_hr', upsert=['employee_id', 'adate'], icon='clock', order='adate DESC, id DESC',
    subtitle='attendance_sub')

Crud('holidays', 'holidays', 'holidays', [
    F(name='name', label='name', type='text', required=1),
    F(name='hdate', label='date', type='date', default_today=1),
    F(name='htype', label='holiday_type', type='select',
      options=[('public', 'holiday_public'), ('company', 'holiday_company')]),
    F(name='notes', label='notes', type='text')],
    [L('name', 'name'), L('hdate', 'date'),
     L('htype', 'holiday_type', fmt=f_map('htype', {'public': 'holiday_public', 'company': 'holiday_company'}))],
    perm='p_hr', icon='calendar', order='hdate DESC', search=['name'])

Crud('bonusded', 'emp_payments', 'bonus_deduction', [
    F(name='employee_id', label='employee', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM employees WHERE active=1 ORDER BY name')),
    F(name='ptype', label='type', type='select', options=[('bonus', 'bonus'), ('deduction', 'deduction')]),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='pdate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('employee_id', 'employee', fmt=f_lookup('employees', 'employee_id')),
     L('ptype', 'type', fmt=f_map('ptype', {'bonus': 'bonus', 'deduction': 'deduction'})),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('pdate', 'date')],
    perm='p_hr', icon='plus', order='pdate DESC')

Crud('advances', 'advances', 'advances', [
    F(name='employee_id', label='employee', type='select', required=1,
      options=lambda req: sel(req, 'SELECT id,name FROM employees WHERE active=1 ORDER BY name')),
    F(name='amount', label='amount', type='number', step='any', required=1),
    F(name='adate', label='date', type='date', default_today=1),
    F(name='note', label='notes', type='textarea')],
    [L('employee_id', 'employee', fmt=f_lookup('employees', 'employee_id')),
     L('amount', 'amount', fmt=f_money('amount'), align='end'), L('adate', 'date'),
     L('deducted', 'deducted', fmt=f_bool('deducted'))],
    perm='p_hr', icon='arrow-down', order='adate DESC')

# --------------------------------------------------------------------------- #
# accounting
# --------------------------------------------------------------------------- #
Crud('accounts', 'accounts_chart', 'chart_of_accounts', [
    F(name='code', label='account_code', type='text'),
    F(name='name', label='account_name', type='text', required=1),
    F(name='name_en', label='item_name_en', type='text'),
    F(name='parent_id', label='parent_account', type='select',
      options=lambda req: sel(req, 'SELECT id, code||\' · \'||name name FROM accounts_chart ORDER BY code')),
    F(name='atype', label='account_type', type='select',
      options=[('asset', 'atype_asset'), ('liability', 'atype_liability'), ('equity', 'atype_equity'),
               ('revenue', 'atype_revenue'), ('expense', 'atype_expense')]),
    F(name='is_group', label='is_group', type='checkbox'),
    F(name='active', label='active', type='checkbox', default=1)],
    [L('code', 'account_code'), L('name', 'account_name'),
     L('parent_id', 'parent_account', fmt=f_lookup('accounts_chart', 'parent_id')),
     L('atype', 'account_type', fmt=f_map('atype', {'asset': 'atype_asset', 'liability': 'atype_liability',
                                                    'equity': 'atype_equity', 'revenue': 'atype_revenue',
                                                    'expense': 'atype_expense'}))],
    perm='p_acc', subtitle='chart_of_accounts_sub', search=['code', 'name'], icon='layers',
    order='code ASC', delete_guard=deny_account_delete)

# --------------------------------------------------------------------------- #
# services
# --------------------------------------------------------------------------- #
Crud('services', 'service_orders', 'service_orders', [
    F(name='customer_name', label='customer_name', type='text', required=1),
    F(name='phone', label='phone', type='text'),
    F(name='device', label='device', type='text'),
    F(name='problem', label='problem', type='textarea'),
    F(name='technician', label='technician', type='text'),
    F(name='cost', label='cost', type='number', step='any', default=0),
    F(name='paid', label='paid', type='number', step='any', default=0),
    F(name='status', label='status', type='select',
      options=[('received', 'service_received'), ('in_progress', 'service_in_progress'),
               ('ready', 'service_ready'), ('delivered', 'service_delivered')]),
    F(name='sdate', label='date', type='date', default_today=1),
    F(name='delivery_date', label='delivery_date', type='date'),
    F(name='notes', label='notes', type='textarea')],
    [L('customer_name', 'customer_name'), L('device', 'device'),
     L('cost', 'cost', fmt=f_money('cost'), align='end'), L('paid', 'paid', fmt=f_money('paid'), align='end'),
     L('status', 'status', fmt=f_map('status', {'received': 'service_received', 'in_progress': 'service_in_progress',
                                                'ready': 'service_ready', 'delivered': 'service_delivered'})),
     L('sdate', 'date')],
    perm='p_services', subtitle='services_sub', search=['customer_name', 'device', 'phone'], icon='wrench',
    order='id DESC')

# --------------------------------------------------------------------------- #
# users & roles
# --------------------------------------------------------------------------- #
Crud('roles', 'roles', 'roles', [
    F(name='name', label='role', type='text', required=1),
    F(name='notes', label='notes', type='text')] +
    [F(name=perm, label=label_key, type='checkbox') for perm, label_key in i18n.PERM_KEYS],
    [L('name', 'role')] + [L(perm, label_key, fmt=f_bool(perm)) for perm, label_key in i18n.PERM_KEYS],
    perm='admin', subtitle='roles_sub', can_delete=True, icon='shield', order='id',
    search=['name'], delete_guard=deny_system_role_delete)

Crud('users', 'users', 'users', [
    F(name='username', label='username', type='text', required=1),
    F(name='password', label='password', type='password'),
    F(name='full_name', label='full_name', type='text', required=1),
    F(name='phone', label='phone', type='text'),
    F(name='email', label='email', type='text'),
    F(name='role_id', label='role', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM roles ORDER BY id')),
    F(name='treasury_id', label='assigned_treasury', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM treasuries ORDER BY name')),
    F(name='store_id', label='assigned_store', type='select',
      options=lambda req: sel(req, 'SELECT id,name FROM stores ORDER BY name')),
    F(name='lang', label='language', type='select', options=[('', 'select'), ('ar', 'arabic'), ('en', 'english')]),
    F(name='is_admin', label='is_admin', type='checkbox'),
    F(name='active', label='active', type='checkbox', default=1),
    F(name='notes', label='notes', type='textarea')],
    [L('username', 'username'), L('full_name', 'full_name'), L('phone', 'phone'),
     L('role_id', 'role', fmt=f_lookup('roles', 'role_id')),
     L('treasury_id', 'assigned_treasury', fmt=f_lookup('treasuries', 'treasury_id')),
     L('store_id', 'assigned_store', fmt=f_lookup('stores', 'store_id')),
     L('is_admin', 'is_admin', fmt=f_bool('is_admin')), L('active', 'active', fmt=f_active('active'))],
    perm='admin', subtitle='users_sub', search=['username', 'full_name', 'phone'], icon='users',
    delete_guard=deny_admin_user_delete, before_save=users_before_save)
