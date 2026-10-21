# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - payroll and attendance helpers."""
import datetime
import time

import core
from core import T, audit, export_rows, flash, guard, one, q, render, scalar, today, x
from db import ex
from framework import redirect, router, url_for


def _month(req):
    month = req.q('month') or req.f('month') or today()[:7]
    try:
        datetime.date.fromisoformat(month + '-01')
    except ValueError:
        month = today()[:7]
    return month


def _employee_rows(req, month):
    rows = []
    for employee in q(req.db, '''SELECT e.*, j.name job_name, p.name package_name, p.basic p_basic,
                                        p.housing p_housing, p.transport p_transport, p.allowance p_allowance
                                 FROM employees e
                                 LEFT JOIN job_roles j ON j.id=e.job_role_id
                                 LEFT JOIN salary_packages p ON p.id=e.package_id
                                 WHERE e.active=1 ORDER BY e.name'''):
        att_days = scalar(req.db, "SELECT COUNT(*) c FROM attendance WHERE employee_id=? AND adate LIKE ? "
                                  "AND status!='absent'", (employee['id'], month + '%'))
        bonus = scalar(req.db, "SELECT COALESCE(SUM(amount),0) v FROM emp_payments WHERE employee_id=? "
                               "AND ptype='bonus' AND pdate LIKE ?", (employee['id'], month + '%'))
        deduction = scalar(req.db, "SELECT COALESCE(SUM(amount),0) v FROM emp_payments WHERE employee_id=? "
                                   "AND ptype='deduction' AND pdate LIKE ?", (employee['id'], month + '%'))
        advance = scalar(req.db, 'SELECT COALESCE(SUM(amount),0) v FROM advances WHERE employee_id=? '
                                 'AND deducted=0', (employee['id'],))
        package_total = sum(core.safe_float(employee.get(key)) for key in
                           ('p_basic', 'p_housing', 'p_transport', 'p_allowance'))
        if core.safe_float(package_total) > 0:
            base = package_total
        elif employee['salary_type'] == 'monthly':
            base = core.safe_float(employee['salary'])
        else:
            base = round(core.safe_float(employee['daily_rate']) * att_days, 2)
        employee.update(att_days=att_days, bonus=bonus, deduction=deduction, advance=advance, base=base,
                        net=round(core.safe_float(base) + core.safe_float(bonus) -
                                  core.safe_float(deduction) - core.safe_float(advance), 2))
        rows.append(employee)
    return rows


@router.route('payroll', '/payroll', ('GET', 'POST'))
def payroll(req):
    blocked = guard(req, 'p_hr')
    if blocked:
        return blocked
    month = _month(req)
    if req.method == 'POST' and req.f('action') == 'run':
        employees = _employee_rows(req, month)
        if not employees:
            flash(req, T(req, 'no_data'), 'warning')
            return redirect(url_for('payroll'), req)
        total = round(sum(core.safe_float(e['net']) for e in employees), 2)
        run_id = x(req.db, 'INSERT INTO payroll_runs(month,run_date,note,user_id,total_net,ctime) '
                           'VALUES(?,?,?,?,?,?)',
                   (month, today(), (req.f('note') or '').strip(), req.user['id'], total, time.time()))
        for employee in employees:
            x(req.db, 'INSERT INTO payslips(run_id,employee_id,base,bonus,deduction,advance,net,att_days) '
                      'VALUES(?,?,?,?,?,?,?,?)',
              (run_id, employee['id'], employee['base'], employee['bonus'], employee['deduction'],
               employee['advance'], employee['net'], employee['att_days']))
            ex(req.db, 'UPDATE advances SET deducted=1 WHERE employee_id=? AND deducted=0',
               (employee['id'],))
        audit(req, 'create', 'payroll_runs', run_id, month)
        flash(req, T(req, 'payroll_done'), 'success')
        return redirect(url_for('payroll_view', rid=run_id), req)
    employees = _employee_rows(req, month)
    runs = q(req.db, '''SELECT pr.*, u.full_name username FROM payroll_runs pr
                        LEFT JOIN users u ON u.id=pr.user_id ORDER BY pr.id DESC LIMIT 24''')
    return render(req, 'payroll.html', title=T(req, 'payroll'), active='payroll', month=month,
                  employees=employees, runs=runs,
                  total=sum(core.safe_float(e['net']) for e in employees))


@router.route('payroll_view', '/payroll/<rid>', ('GET',))
def payroll_view(req, rid):
    blocked = guard(req, 'p_hr')
    if blocked:
        return blocked
    run = one(req.db, 'SELECT * FROM payroll_runs WHERE id=?', (rid,))
    if not run:
        return core.not_found()
    rows = q(req.db, '''SELECT ps.*, e.name employee_name, e.salary_type, j.name job_name
                        FROM payslips ps JOIN employees e ON e.id=ps.employee_id
                        LEFT JOIN job_roles j ON j.id=e.job_role_id
                        WHERE ps.run_id=? ORDER BY e.name''', (rid,))
    if req.q('export'):
        data = [[T(req, 'payroll_month'), run['month']],
                [T(req, 'employee'), T(req, 'base_salary'), T(req, 'bonus'), T(req, 'deduction'),
                 T(req, 'advance'), T(req, 'net_salary')]]
        data += [[r['employee_name'], r['base'], r['bonus'], r['deduction'], r['advance'], r['net']]
                 for r in rows]
        data.append([T(req, 'total'), '', '', '', '', sum(core.safe_float(r['net']) for r in rows)])
        return export_rows(f"payroll-{run['month']}.xlsx", data)
    return render(req, 'payroll_view.html', title=T(req, 'payroll_runs'), active='payroll', run=run,
                  rows=rows, total=sum(core.safe_float(r['net']) for r in rows))


@router.route('payslip', '/payroll/<rid>/payslip/<eid>', ('GET',))
def payslip(req, rid, eid):
    blocked = guard(req, 'p_hr')
    if blocked:
        return blocked
    row = one(req.db, '''SELECT ps.*, e.name employee_name, e.phone, e.salary_type, j.name job_name
                         FROM payslips ps JOIN employees e ON e.id=ps.employee_id
                         LEFT JOIN job_roles j ON j.id=e.job_role_id
                         WHERE ps.run_id=? AND ps.employee_id=?''', (rid, eid))
    if not row:
        return core.not_found()
    run = one(req.db, 'SELECT * FROM payroll_runs WHERE id=?', (rid,))
    return render(req, 'payslip.html', title=T(req, 'payslip'), row=row, run=run, active='payroll')


@router.route('attendance_bulk', '/attendance/bulk', ('GET', 'POST'))
def attendance_bulk(req):
    blocked = guard(req, 'p_hr')
    if blocked:
        return blocked
    employees = q(req.db, 'SELECT id, name FROM employees WHERE active=1 ORDER BY name')
    if req.method == 'POST':
        day = req.f('adate') or today()
        status = req.f('status') or 'present'
        saved = 0
        for employee in employees:
            if req.f(f'emp_{employee["id"]}'):
                existing = one(req.db, 'SELECT id FROM attendance WHERE employee_id=? AND adate=?',
                               (employee['id'], day))
                if existing:
                    ex(req.db, 'UPDATE attendance SET status=? WHERE id=?', (status, existing['id']))
                else:
                    x(req.db, 'INSERT INTO attendance(employee_id,adate,status,note) VALUES(?,?,?,?)',
                      (employee['id'], day, status, ''))
                saved += 1
        audit(req, 'create', 'attendance', 0, f'bulk {saved} {day}')
        flash(req, T(req, 'saved') + f' ({saved})', 'success')
        return redirect(url_for('attendance'), req)
    return render(req, 'attendance_bulk.html', title=T(req, 'attendance'), active='attendance',
                  employees=employees)
