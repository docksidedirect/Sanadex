# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - offline licence keys.

The administrator generates a key inside the app, signed with the product
secret (HMAC-SHA256). The same key can be verified anywhere without a server,
which keeps the product usable offline while still allowing editions, seat
counts, expiry dates and machine binding.

Set ``SANADEX_LICENSE_SECRET`` to your own value on the machine that issues
keys; keep it private so only you can mint licences.
"""
import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import time
import uuid

PREFIX = 'SNDX'
PRODUCT_SECRET = os.environ.get('SANADEX_LICENSE_SECRET',
                                'sanadex-wanis-online-license-v1-change-me')
EDITIONS = ('trial', 'standard', 'pro', 'enterprise')
TRIAL_DAYS = 14
SIGNATURE_BYTES = 12


def _b32encode(data):
    return base64.b32encode(data).decode().rstrip('=')


def _b32decode(text):
    padding = '=' * (-len(text) % 8)
    return base64.b32decode(text + padding)


def _signature(payload_bytes):
    return hmac.new(PRODUCT_SECRET.encode(), payload_bytes, hashlib.sha256).digest()[:SIGNATURE_BYTES]


def machine_id():
    """Stable, non-sensitive fingerprint of the current machine."""
    raw = '|'.join([socket.gethostname() or '', platform.machine() or '',
                    platform.system() or '', f'{uuid.getnode():x}'])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12].upper()


def generate(customer, edition='standard', seats=1, days=365, expires='', machine=''):
    """Return a formatted licence key."""
    edition = edition if edition in EDITIONS else 'standard'
    try:
        seats = max(0, int(seats))
    except (TypeError, ValueError):
        seats = 1
    issued = int(time.time())
    if not expires:
        expires = time.strftime('%Y-%m-%d', time.localtime(issued + max(1, int(days or 365)) * 86400))
    payload = {
        'c': (customer or '').strip()[:60],
        'e': edition,
        's': seats,
        'i': issued,
        'x': expires,
        'm': (machine or '').strip().upper()[:16],
    }
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    token = _b32encode(raw) + '.' + _b32encode(_signature(raw))
    grouped = '-'.join(token[i:i + 5] for i in range(0, len(token), 5))
    return f'{PREFIX}-{grouped}'


def clean(key):
    text = (key or '').strip().upper().replace(' ', '').replace('\n', '')
    if text.startswith(PREFIX + '-'):
        text = text[len(PREFIX) + 1:]
    return text.replace('-', '')


def parse(key):
    """Decode a key without trusting it. Returns (payload, signature_ok)."""
    text = clean(key)
    if '.' not in text:
        return None, False
    body, signature = text.split('.', 1)
    try:
        raw = _b32decode(body)
        payload = json.loads(raw.decode('utf-8'))
        given = _b32decode(signature)
    except Exception:
        return None, False
    if not isinstance(payload, dict):
        return None, False
    return payload, hmac.compare_digest(_signature(raw), given)


def verify(key, check_machine=True):
    payload, signature_ok = parse(key)
    result = {'ok': False, 'reason': '', 'payload': payload or {}, 'days_left': 0,
              'machine': machine_id()}
    if not payload:
        result['reason'] = 'malformed'
        return result
    if not signature_ok:
        result['reason'] = 'signature'
        return result
    if payload.get('e') not in EDITIONS:
        result['reason'] = 'edition'
        return result
    try:
        expires = time.strptime(str(payload.get('x')), '%Y-%m-%d')
        expiry_ts = time.mktime(expires)
    except (ValueError, TypeError):
        result['reason'] = 'expiry'
        return result
    days_left = int((expiry_ts - time.time()) // 86400)
    result['days_left'] = days_left
    if expiry_ts < time.time():
        result['reason'] = 'expired'
        return result
    bound = str(payload.get('m') or '').upper()
    if check_machine and bound and bound != machine_id():
        result['reason'] = 'machine'
        return result
    result['ok'] = True
    return result


# --------------------------------------------------------------------------- #
# persistence helpers
# --------------------------------------------------------------------------- #
def _row_to_license(row):
    if not row:
        return None
    return {'id': row['id'], 'key': row['key'], 'customer': row['customer'],
            'edition': row['edition'], 'seats': row['seats'], 'issued': row['issued'],
            'expires': row['expires'], 'machine': row['machine'], 'status': row['status'],
            'created_at': row['created_at'], 'created_by': row['created_by'],
            'note': row['note']}


def active_license(conn):
    row = conn.execute('SELECT * FROM licenses WHERE status=? ORDER BY id DESC LIMIT 1',
                       ('active',)).fetchone()
    return _row_to_license(dict(row) if row else None)


def history(conn, limit=50):
    rows = conn.execute('SELECT * FROM licenses ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [_row_to_license(dict(row)) for row in rows]


def activate(conn, key, username='', allow_machine_mismatch=False):
    """Verify and store a key. Returns the verify result."""
    result = verify(key, check_machine=not allow_machine_mismatch)
    if not result['ok']:
        return result
    payload = result['payload']
    conn.execute("UPDATE licenses SET status='revoked' WHERE status='active'")
    conn.execute('INSERT INTO licenses(key,customer,edition,seats,issued,expires,machine,status,'
                 'created_at,created_by,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (key.strip(), payload.get('c', ''), payload.get('e', 'standard'),
                  payload.get('s', 1), _iso(payload.get('i')), payload.get('x', ''),
                  payload.get('m', ''), 'active', time.time(), username, ''))
    conn.commit()
    return result


def revoke(conn, license_id):
    conn.execute("UPDATE licenses SET status='revoked' WHERE id=?", (license_id,))
    conn.commit()


def _iso(timestamp):
    try:
        return time.strftime('%Y-%m-%d', time.localtime(float(timestamp)))
    except (TypeError, ValueError):
        return time.strftime('%Y-%m-%d')


def state(conn, company=None):
    """Current licence state used by the UI: licensed / trial / expired."""
    company = company or {}
    try:
        installed = conn.execute("SELECT v FROM app_meta WHERE k='installed_at'").fetchone()
        installed_at = float(dict(installed)['v']) if installed else time.time()
    except Exception:
        installed_at = time.time()
    current = active_license(conn)
    if current:
        checked = verify(current['key'], check_machine=False)
        days_left = checked['days_left']
        expired = current['status'] != 'active' or not checked['ok']
        if not expired:
            return {'state': 'licensed', 'edition': current['edition'],
                    'customer': current['customer'], 'seats': current['seats'],
                    'expires': current['expires'], 'days_left': max(days_left, 0),
                    'machine': current['machine'], 'key': current['key'],
                    'trial_days_left': 0}
        return {'state': 'expired', 'edition': current['edition'],
                'customer': current['customer'], 'seats': current['seats'],
                'expires': current['expires'], 'days_left': 0,
                'machine': current['machine'], 'key': current['key'],
                'trial_days_left': 0}
    trial_left = int((installed_at + TRIAL_DAYS * 86400 - time.time()) // 86400)
    if trial_left < 0:
        return {'state': 'expired', 'edition': 'trial', 'customer': '', 'seats': 1,
                'expires': _iso(installed_at + TRIAL_DAYS * 86400), 'days_left': 0,
                'machine': '', 'key': '', 'trial_days_left': 0}
    return {'state': 'trial', 'edition': 'trial', 'customer': '', 'seats': 1,
            'expires': _iso(installed_at + TRIAL_DAYS * 86400), 'days_left': trial_left,
            'machine': '', 'key': '', 'trial_days_left': trial_left}


def license_text(key):
    """Plain-text licence document for download."""
    payload, ok = parse(key)
    lines = ['Sanadex.wanis.online — licence key', '=' * 38,
             f'Key      : {key}',
             f'Customer : {(payload or {}).get("c", "")}',
             f'Edition  : {(payload or {}).get("e", "")}',
             f'Seats    : {(payload or {}).get("s", "")}',
             f'Expires  : {(payload or {}).get("x", "")}',
             f'Machine  : {(payload or {}).get("m", "") or "any"}',
             f'Valid    : {"yes" if ok else "no"}',
             '', 'Activate it from Settings → Licence inside the application.']
    return '\n'.join(lines) + '\n'

# --------------------------------------------------------------------------- #
# per-user licensing: administrators are perpetual, everybody else gets a trial
# --------------------------------------------------------------------------- #
def user_state(conn, user, company=None):
    """Return the licence state that applies to *this* user.

    - administrators always hold a lifetime licence (the owner of the install)
    - a user granted a permanent licence (users.licensed) is licensed
    - a valid installation licence covers every user
    - otherwise the user has a personal TRIAL_DAYS window from first login
    """
    user = user or {}
    if user.get('is_admin'):
        return {'state': 'lifetime', 'edition': 'lifetime', 'customer': '', 'seats': 0,
                'expires': '', 'days_left': None, 'machine': '', 'key': '',
                'trial_days_left': 0, 'scope': 'admin'}
    if user.get('licensed'):
        installation = state(conn, company)
        return {'state': 'licensed', 'edition': installation.get('edition', 'standard'),
                'customer': installation.get('customer', ''), 'seats': installation.get('seats', 1),
                'expires': installation.get('expires', ''), 'days_left': installation.get('days_left', 0),
                'machine': '', 'key': '', 'trial_days_left': 0, 'scope': 'user'}
    installation = state(conn, company)
    if installation.get('state') == 'licensed':
        return {'state': 'licensed', 'edition': installation['edition'],
                'customer': installation['customer'], 'seats': installation['seats'],
                'expires': installation['expires'], 'days_left': installation['days_left'],
                'machine': installation.get('machine', ''), 'key': installation.get('key', ''),
                'trial_days_left': 0, 'scope': 'installation'}
    started = float(user.get('trial_started') or 0)
    if started <= 0:
        days_left = TRIAL_DAYS
    else:
        elapsed = max(0.0, time.time() - started)
        days_left = TRIAL_DAYS - int(elapsed // 86400)
    days_left = max(0, days_left)
    state_name = 'trial' if days_left > 0 else 'trial_expired'
    expires = _iso((started or time.time()) + TRIAL_DAYS * 86400)
    return {'state': state_name, 'edition': 'trial', 'customer': user.get('full_name') or user.get('username') or '',
            'seats': 1, 'expires': expires, 'days_left': days_left, 'machine': '', 'key': '',
            'trial_days_left': days_left, 'scope': 'user'}


def start_trial(conn, user_id):
    """Mark the first login of a user (idempotent)."""
    conn.execute('UPDATE users SET trial_started=? WHERE id=? AND (trial_started IS NULL OR trial_started=0)',
                 (time.time(), user_id))
    conn.commit()


def reset_trial(conn, user_id):
    """Give a user a fresh TRIAL_DAYS window."""
    conn.execute('UPDATE users SET trial_started=?, licensed=0 WHERE id=?', (time.time(), user_id))
    conn.commit()


def set_user_licensed(conn, user_id, licensed=True):
    conn.execute('UPDATE users SET licensed=? WHERE id=?', (1 if licensed else 0, user_id))
    conn.commit()


def user_trials(conn):
    """Every non-admin user with their current trial state."""
    rows = conn.execute("SELECT id, username, full_name, trial_started, licensed, active FROM users "
                        "WHERE is_admin=0 ORDER BY username").fetchall()
    output = []
    for row in rows:
        user = dict(row)
        user['licence'] = user_state(conn, user)
        output.append(user)
    return output
