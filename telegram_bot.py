# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - Telegram admin bot.

A small long-polling bot that lets the owner run the commercial side from
Telegram: daily digest, new-trial alerts, pending subscriptions, approvals,
renewals, expiry warnings and a health check.

Configuration lives in Settings → Licence → Telegram alerts (or in the
SANADEX_TELEGRAM_TOKEN / SANADEX_TELEGRAM_CHAT_ID environment variables).
The bot only answers the configured administrator chat ids.
"""
import json
import os
import platform
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import db
import license as license_lib

API_BASE = (os.environ.get('SANADEX_TELEGRAM_API') or 'https://api.telegram.org').rstrip('/')
APP_VERSION = '3.0'
_lock = threading.Lock()
_started = False


# --------------------------------------------------------------------------- #
# configuration + HTTP
# --------------------------------------------------------------------------- #
def config():
    row = {}
    try:
        conn = db.connect()
        try:
            row = db.one(conn, 'SELECT name, currency, telegram_enabled, telegram_token, '
                               'telegram_chat_id, telegram_digest_hour, public_url '
                               'FROM company WHERE id=1') or {}
        finally:
            conn.close()
    except Exception:
        row = {}
    token = (os.environ.get('SANADEX_TELEGRAM_TOKEN') or row.get('telegram_token') or '').strip()
    chat_ids = (os.environ.get('SANADEX_TELEGRAM_CHAT_ID') or row.get('telegram_chat_id') or '').strip()
    enabled = bool(int(row.get('telegram_enabled') or 0)) or bool(
        os.environ.get('SANADEX_TELEGRAM_TOKEN'))
    try:
        digest_hour = max(0, min(23, int(
            os.environ.get('SANADEX_TELEGRAM_DIGEST_HOUR')
            or row.get('telegram_digest_hour') or 21)))
    except (TypeError, ValueError):
        digest_hour = 21
    return {
        'enabled': enabled,
        'token': token,
        'chat_ids': [c.strip() for c in chat_ids.replace(';', ',').split(',') if c.strip()],
        'digest_hour': digest_hour,
        'public_url': (os.environ.get('SANADEX_PUBLIC_URL')
                       or row.get('public_url') or '').strip().rstrip('/'),
        'currency': (row.get('currency') or 'EGP').strip() or 'EGP',
        'company': row.get('name') or 'Sanadex.wanis.online',
    }


def _api(method, payload, token):
    url = f'{API_BASE}/bot{token}/{method}'
    data = urllib.parse.urlencode(payload or {}).encode('utf-8')
    request = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.loads(response.read().decode('utf-8') or '{}')


def send(cfg, text, chat_id=None, keyboard=None):
    """Send one plain-text message. Never raises (the app must keep working)."""
    token = cfg.get('token')
    target = chat_id or (cfg.get('chat_ids') or [None])[0]
    if not token or not target:
        return False
    payload = {'chat_id': target, 'text': (text or '')[:4000],
               'disable_web_page_preview': 'true'}
    if keyboard:
        payload['reply_markup'] = json.dumps(keyboard)
    try:
        _api('sendMessage', payload, token)
        return True
    except Exception:
        return False


def _answer(cfg, callback_id, text=''):
    try:
        _api('answerCallbackQuery', {'callback_query_id': callback_id, 'text': (text or '')[:180]},
             cfg.get('token'))
    except Exception:
        pass


def _authorized(cfg, chat_id):
    return str(chat_id) in cfg.get('chat_ids', [])


def recent_chats(token, limit=100):
    """Chats that recently messaged the bot — used by the 'Detect chat ID' button."""
    if not token:
        return []
    try:
        data = _api('getUpdates', {'timeout': 0, 'limit': limit}, token)
    except Exception:
        return []
    if not data.get('ok'):
        return []
    found = {}
    for update in data.get('result', []):
        message = update.get('message') or update.get('edited_message') or {}
        if not message and update.get('callback_query'):
            message = (update.get('callback_query') or {}).get('message') or {}
        chat = message.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue
        name = ' '.join(part for part in (chat.get('first_name'), chat.get('last_name'))
                        if part) or chat.get('title') or chat.get('username') or ''
        found[str(chat_id)] = {'id': str(chat_id), 'name': name,
                               'username': chat.get('username') or ''}
    return list(found.values())


# --------------------------------------------------------------------------- #
# keyboards + menus
# --------------------------------------------------------------------------- #
def _button(text, data):
    return {'text': text, 'callback_data': data}


def _url_button(text, url):
    return {'text': text, 'url': url}


def _admin_row(cfg):
    if not cfg.get('public_url'):
        return []
    return [[_url_button('Open admin', cfg['public_url'] + '/license')]]


def menu_keyboard(cfg):
    rows = [
        [_button('📊 Stats', 'menu:stats'), _button('⏳ Pending', 'menu:pending')],
        [_button('🔎 Find', 'prompt:find'), _button('📅 Expiry', 'menu:expiry')],
        [_button('📨 Digest now', 'menu:digest'), _button('❤️ Health', 'menu:health')],
        [_button('❓ Help', 'menu:help')],
    ]
    return {'inline_keyboard': rows + _admin_row(cfg)}


def _actions_keyboard(rows=None):
    """Only contextual action buttons — the full menu is requested explicitly."""
    rows = [row for row in (rows or []) if row]
    return {'inline_keyboard': rows} if rows else None


COMMANDS = (
    ('menu', 'Open the bot menu'),
    ('stats', 'Revenue and licence counts'),
    ('pending', 'Subscriptions waiting for approval'),
    ('find', 'Look up a subscription'),
    ('approve', 'Approve a pending subscription'),
    ('renew', 'Renew a subscription'),
    ('digest', 'Send the daily digest now'),
    ('expiry', 'Subscriptions expiring this week'),
    ('health', 'Check this installation'),
    ('admin', 'Open the admin licence screen'),
    ('help', 'Show available commands'),
)

HELP_TEXT = ('🤖 Sanadex admin bot\n\n'
             'Use the ☰ Menu button or /menu to open the menu. '
             'Alerts are sent without extra buttons.\n\n'
             '/stats — revenue and licence counts\n'
             '/pending — subscriptions waiting for approval\n'
             '/find <user or id> — look up a subscription\n'
             '/approve <id> — approve a pending subscription\n'
             '/renew <user> [1m|3m|6m|1y|lifetime] — renew a subscription\n'
             '/digest — send the daily digest now\n'
             '/expiry — show subscriptions expiring this week\n'
             '/health — check this installation\n'
             '/admin — open the admin licence screen\n'
             '/help — show available commands')


def set_profile(cfg):
    """Publish the bot description and native Menu commands (best effort)."""
    token = cfg.get('token')
    if not token:
        return False
    commands = json.dumps([{'command': name, 'description': text} for name, text in COMMANDS])
    ok = False
    for method, payload in (
        ('setMyCommands', {'commands': commands}),
        ('setChatMenuButton', {'menu_button': json.dumps({'type': 'commands'})}),
        ('setMyDescription', {'description': 'Sanadex admin bot — trials, subscriptions, '
                                            'renewals and daily reports.'}),
        ('setShortDescription', {'description': 'Manage Sanadex subscriptions from Telegram.'}),
    ):
        try:
            _api(method, payload, token)
            ok = True
        except Exception:
            pass
    return ok


# --------------------------------------------------------------------------- #
# data builders (pure enough to test without Telegram)
# --------------------------------------------------------------------------- #
def _money(cfg, value):
    return f'{float(value or 0):,.0f} {cfg.get("currency") or "EGP"}'


def _date(ts):
    try:
        return time.strftime('%Y-%m-%d', time.localtime(float(ts)))
    except (TypeError, ValueError):
        return '—'


def _active_sub_rows(conn):
    return db.q(conn, "SELECT * FROM subscriptions WHERE status='active' "
                      "AND (expires=0 OR expires>?)", (time.time(),))


def _active_accounts(conn):
    users = db.q(conn, 'SELECT id, licensed FROM users WHERE is_admin=0 AND active=1')
    active_ids = {int(r['user_id']) for r in _active_sub_rows(conn)}
    return sum(1 for u in users if u.get('licensed') or int(u['id']) in active_ids)


def _expiring_rows(conn, days=7):
    now = time.time()
    return db.q(conn, "SELECT s.*, u.username, u.full_name FROM subscriptions s "
                      "JOIN users u ON u.id=s.user_id WHERE s.status='active' AND s.expires>0 "
                      "AND s.expires>=? AND s.expires<=? ORDER BY s.expires LIMIT 20",
                 (now, now + days * 86400))


def build_stats(conn, cfg):
    day_start = time.mktime(time.strptime(time.strftime('%Y-%m-%d'), '%Y-%m-%d'))
    today = db.q(conn, "SELECT price FROM subscriptions WHERE status='active' AND created_at>=?",
                 (day_start,))
    revenue = sum(float(r.get('price') or 0) for r in today)
    active = _active_accounts(conn)
    active_subs = len({int(r['user_id']) for r in _active_sub_rows(conn)})
    pending = len(license_lib.pending_requests(conn))
    expiring = _expiring_rows(conn)
    lines = [f"📊 Daily digest — {time.strftime('%Y-%m-%d')}",
             f'Revenue: {_money(cfg, revenue)}',
             f'Active licences: {active}',
             f'Subscriptions: {active_subs + pending} '
             f'({active_subs} active, {pending} pending)']
    if expiring:
        lines.append('')
        lines.append('Expiring this week:')
        for row in expiring:
            lines.append(f"• @{row.get('username')} — {_date(row.get('expires'))}")
    else:
        lines.append('No subscriptions expiring this week.')
    return '\n'.join(lines), None


def build_pending(conn, cfg):
    rows = license_lib.pending_requests(conn)
    if not rows:
        return '✅ No pending subscriptions.', None
    lines = [f'⏳ Pending subscriptions ({len(rows)})', '']
    keyboard = []
    for row in rows[:10]:
        info = license_lib.plan(row.get('plan'))
        name = row.get('full_name') or row.get('username') or f"user {row.get('user_id')}"
        lines.append(f"• {name} (@{row.get('username')}) — "
                     f"{info['en'] if info else row.get('plan')} · "
                     f"{_money(cfg, row.get('price'))} · {_date(row.get('created_at'))}")
        keyboard.append([
            _button(f"✅ {row.get('username')}", f"approve:{row.get('id')}"),
            _button('✖ Reject', f"reject:{row.get('id')}"),
        ])
    return '\n'.join(lines), _actions_keyboard(keyboard)


def _find_user(conn, term):
    term = (term or '').strip().lstrip('@')
    if not term:
        return None
    if term.isdigit():
        return db.one(conn, 'SELECT * FROM users WHERE id=?', (int(term),))
    return db.one(conn, 'SELECT * FROM users WHERE username=? OR phone=? OR full_name LIKE ? '
                        'ORDER BY id LIMIT 1', (term.lower(), term, f'%{term}%'))


def build_find(conn, cfg, term):
    user = _find_user(conn, term)
    if not user:
        return f'No account matches “{term}”.', None
    state = license_lib.user_state(conn, user)
    sub = license_lib.active_subscription(conn, user['id'])
    info = license_lib.plan(sub['plan']) if sub else None
    lines = [f"👤 {user.get('full_name') or user.get('username')} (@{user.get('username')})",
             f"Account: {'active' if user.get('active') else 'disabled'}"]
    if user.get('is_admin') or state['state'] == 'lifetime':
        lines += ['Plan: administrator lifetime licence', 'Status: lifetime']
    elif state['state'] == 'licensed' and not sub:
        lines += ['Plan: lifetime licence', 'Status: licensed']
    elif sub:
        lines += [f"Plan: {info['en'] if info else sub.get('plan')}",
                  f"Status: {state['state']}",
                  f"Price: {_money(cfg, sub.get('price'))}",
                  f"Started: {_date(sub.get('starts'))}",
                  f"Expires: {'never' if sub.get('days_left') is None else _date(sub.get('expires'))}",
                  f"Days left: {sub.get('days_left') if sub.get('days_left') is not None else '∞'}"]
    else:
        lines += ['Plan: 14-day trial', f"Status: {state['state']}",
                  f"Trial started: {_date(user.get('trial_started'))}",
                  f"Trial days left: {state.get('days_left') if state.get('days_left') is not None else '∞'}"]
    keyboard = [[
        _button('+30 days', f"renew:{user['id']}:1m"),
        _button('+3 months', f"renew:{user['id']}:3m"),
        _button('+1 year', f"renew:{user['id']}:1y"),
    ]]
    return '\n'.join(lines), _actions_keyboard(keyboard)


def build_expiry(conn, cfg, days=7):
    rows = _expiring_rows(conn, days)
    if not rows:
        return '✅ No subscriptions expiring this week.', None
    lines = [f'📅 Expiring in the next {days} days', '']
    keyboard = []
    for row in rows[:10]:
        info = license_lib.plan(row.get('plan'))
        left = max(0, int((float(row['expires']) - time.time()) // 86400))
        lines.append(f"• @{row.get('username')} — {info['en'] if info else row.get('plan')} · "
                     f"{_date(row['expires'])} ({left} days)")
        keyboard.append([_button(f"Renew @{row.get('username')} +30d",
                                 f"renew:{row.get('user_id')}:1m")])
    return '\n'.join(lines), _actions_keyboard(keyboard)


def build_health(conn, cfg):
    try:
        users = db.scalar(conn, 'SELECT COUNT(*) c FROM users')
        active_users = db.scalar(conn, 'SELECT COUNT(*) c FROM users WHERE active=1')
        licensed = license_lib.state(conn)
        pending = len(license_lib.pending_requests(conn))
        lines = ['❤️ Sanadex health', 'Database: OK',
                 f'Users: {users} ({active_users} active)',
                 f'Licence: {licensed.get("state")}',
                 f'Subscriptions: {len(_active_sub_rows(conn))} active, {pending} pending',
                 f'Server time: {time.strftime("%Y-%m-%d %H:%M")}',
                 f'Version: {APP_VERSION}']
        return '\n'.join(lines), None
    except Exception as exc:
        return f'💔 Health check failed: {str(exc)[:300]}', None


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def perform_approve(conn, sub_id, by='telegram'):
    sub = db.one(conn, 'SELECT * FROM subscriptions WHERE id=?', (int(sub_id),))
    if not sub or sub.get('status') != 'pending':
        return 'This request is no longer pending.'
    user = db.one(conn, 'SELECT * FROM users WHERE id=?', (sub['user_id'],)) or {}
    result = license_lib.activate_subscription(conn, sub['user_id'], sub['plan'], by)
    if not result:
        return 'Could not activate this subscription.'
    info = license_lib.plan(sub['plan'])
    expires = 'never' if result.get('days_left') is None else _date(result.get('expires'))
    db.ex(conn, 'INSERT INTO audit_log(ts,user_id,username,action,entity,entity_id,detail,ip) '
                'VALUES(?,?,?,?,?,?,?,?)',
          (time.time(), 0, by, 'update', 'licenses', sub['user_id'],
           f"telegram approved {sub['plan']} for {user.get('username')}", 'telegram'))
    return (f"✅ Approved {info['en'] if info else sub['plan']} for "
            f"@{user.get('username') or sub['user_id']} — expires {expires}.")


def perform_reject(conn, sub_id, by='telegram'):
    sub = db.one(conn, 'SELECT * FROM subscriptions WHERE id=?', (int(sub_id),))
    if not sub or sub.get('status') != 'pending':
        return 'This request is no longer pending.'
    db.ex(conn, "UPDATE subscriptions SET status='cancelled' WHERE id=?", (int(sub_id),))
    user = db.one(conn, 'SELECT username FROM users WHERE id=?', (sub['user_id'],)) or {}
    return f"✖ Rejected the request for @{user.get('username') or sub['user_id']}."


def perform_renew(conn, cfg, user_id, plan_key='1m', by='telegram'):
    user = db.one(conn, 'SELECT * FROM users WHERE id=?', (int(user_id),))
    if not user:
        return 'Account not found.'
    info = license_lib.plan(plan_key)
    if not info:
        return 'Unknown plan. Use 1m, 3m, 6m, 1y or lifetime.'
    result = license_lib.activate_subscription(conn, user['id'], plan_key, by)
    if not result:
        return f"@{user.get('username')} already has a lifetime licence."
    db.ex(conn, 'INSERT INTO audit_log(ts,user_id,username,action,entity,entity_id,detail,ip) '
                'VALUES(?,?,?,?,?,?,?,?)',
          (time.time(), 0, by, 'update', 'licenses', user['id'],
           f'telegram renewed {plan_key} for {user.get("username")}', 'telegram'))
    expires = 'never' if result.get('days_left') is None else _date(result.get('expires'))
    return (f"🔄 Renewed @{user.get('username')} — {info['en']} · "
            f"{_money(cfg, info['price'])} · expires {expires}.")


def perform_digest(cfg):
    conn = db.connect()
    try:
        return build_stats(conn, cfg)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Telegram update handling
# --------------------------------------------------------------------------- #
def handle_update(update, cfg=None):
    cfg = cfg or config()
    if 'callback_query' in update:
        callback = update['callback_query']
        chat_id = ((callback.get('message') or {}).get('chat') or {}).get('id')
        if not _authorized(cfg, chat_id):
            _answer(cfg, callback.get('id'), 'Not authorised')
            return
        data = callback.get('data') or ''
        _answer(cfg, callback.get('id'))
        conn = db.connect()
        try:
            if data.startswith('menu:') or data.startswith('prompt:'):
                _send_menu(cfg, data, conn)
            elif data.startswith('approve:'):
                send(cfg, perform_approve(conn, data.split(':', 1)[1]))
            elif data.startswith('reject:'):
                send(cfg, perform_reject(conn, data.split(':', 1)[1]))
            elif data.startswith('renew:'):
                _, uid, plan_key = (data.split(':') + ['1m'])[:3]
                send(cfg, perform_renew(conn, cfg, uid, plan_key))
        finally:
            conn.close()
        return
    message = update.get('message') or {}
    chat_id = (message.get('chat') or {}).get('id')
    if not _authorized(cfg, chat_id):
        if message.get('text', '').startswith('/start'):
            send(cfg, 'This bot is private. Ask the administrator for access.', chat_id=chat_id)
        return
    text = (message.get('text') or '').strip()
    if not text:
        return
    command, args = _parse(text)
    conn = db.connect()
    try:
        if command in ('/start', '/menu'):
            send(cfg, HELP_TEXT, keyboard=menu_keyboard(cfg))
        elif command == '/help':
            send(cfg, HELP_TEXT, keyboard=menu_keyboard(cfg))
        elif command == '/stats':
            body, keyboard = build_stats(conn, cfg)
            send(cfg, body, keyboard=keyboard)
        elif command == '/pending':
            body, keyboard = build_pending(conn, cfg)
            send(cfg, body, keyboard=keyboard)
        elif command == '/find':
            body, keyboard = build_find(conn, cfg, args[0] if args else '')
            send(cfg, body, keyboard=keyboard)
        elif command == '/approve':
            send(cfg, perform_approve(conn, args[0]) if args and args[0].isdigit()
                 else 'Usage: /approve <subscription id> — use /pending to see ids.')
        elif command == '/renew':
            if not args:
                send(cfg, 'Usage: /renew <username or id> [1m|3m|6m|1y|lifetime]')
            else:
                user = _find_user(conn, args[0])
                plan_key = args[1] if len(args) > 1 else '1m'
                send(cfg, perform_renew(conn, cfg, user['id'], plan_key)
                     if user else f'No account matches “{args[0]}”.')
        elif command == '/digest':
            body, keyboard = build_stats(conn, cfg)
            send(cfg, body, keyboard=keyboard)
        elif command == '/expiry':
            body, keyboard = build_expiry(conn, cfg)
            send(cfg, body, keyboard=keyboard)
        elif command == '/health':
            body, keyboard = build_health(conn, cfg)
            send(cfg, body, keyboard=keyboard)
        elif command == '/admin':
            if cfg.get('public_url'):
                send(cfg, 'Open the admin licence screen:\n'
                          f"{cfg['public_url']}/license")
            else:
                send(cfg, 'Set SANADEX_PUBLIC_URL (or Public URL on the Licence screen) first.')
        else:
            send(cfg, 'Unknown command. Send /help or use the ☰ Menu.')
    finally:
        conn.close()


def _parse(text):
    parts = (text or '').strip().split()
    if not parts:
        return '', []
    return parts[0].split('@')[0].lower(), parts[1:]


def _send_menu(cfg, data, conn):
    action = data.split(':', 1)[1] if ':' in data else 'help'
    if action == 'stats':
        body, keyboard = build_stats(conn, cfg)
    elif action == 'pending':
        body, keyboard = build_pending(conn, cfg)
    elif action == 'expiry':
        body, keyboard = build_expiry(conn, cfg)
    elif action == 'health':
        body, keyboard = build_health(conn, cfg)
    elif action == 'digest':
        body, keyboard = build_stats(conn, cfg)
    elif action == 'find':
        body, keyboard = 'Send /find <username, phone or id>.', menu_keyboard(cfg)
    else:
        body, keyboard = HELP_TEXT, menu_keyboard(cfg)
    send(cfg, body, keyboard=keyboard)


# --------------------------------------------------------------------------- #
# notifications + background loops
# --------------------------------------------------------------------------- #
def notify_new_account(user_id, requested_plan=''):
    """Announce a new trial/sign-up to the administrator (non-blocking)."""
    cfg = config()
    if not cfg.get('enabled') or not cfg.get('token') or not cfg.get('chat_ids'):
        return
    try:
        conn = db.connect()
        try:
            user = db.one(conn, 'SELECT * FROM users WHERE id=?', (int(user_id),)) or {}
        finally:
            conn.close()
    except Exception:
        user = {}
    info = license_lib.plan(requested_plan) if requested_plan else None
    lines = ['🎁 New trial',
             f"User: {user.get('full_name') or user.get('username') or user_id} "
             f"(@{user.get('username') or ''})",
             f"Requested plan: {info['en'] if info else '14-day trial'}",
             f"Device: {platform.node() or 'server'} ({platform.system().lower() or platform.platform()})",
             f"Device ID: {license_lib.machine_id()}",
             f"Time: {time.strftime('%Y-%m-%d %H:%M')}"]
    threading.Thread(target=send, args=(cfg, '\n'.join(lines)), daemon=True).start()


def _set_meta(conn, key, value):
    cur = conn.execute('UPDATE app_meta SET v=? WHERE k=?', (str(value), key))
    if not cur.rowcount:
        conn.execute('INSERT INTO app_meta(k,v) VALUES(?,?)', (key, str(value)))
    conn.commit()


def _digest_loop():
    while True:
        try:
            cfg = config()
            if cfg.get('enabled') and cfg.get('token') and cfg.get('chat_ids'):
                now = time.localtime()
                if now.tm_hour >= int(cfg.get('digest_hour') or 21):
                    conn = db.connect()
                    try:
                        last = db.one(conn, "SELECT v FROM app_meta WHERE k='telegram_last_digest'")
                        today = time.strftime('%Y-%m-%d')
                        if not last or str(last.get('v')) != today:
                            body, keyboard = build_stats(conn, cfg)
                            if send(cfg, body, keyboard=keyboard):
                                _set_meta(conn, 'telegram_last_digest', today)
                    finally:
                        conn.close()
        except Exception:
            pass
        time.sleep(60)


def _poll_loop():
    offset = 0
    profile_token = None
    while True:
        cfg = config()
        if not (cfg.get('enabled') and cfg.get('token') and cfg.get('chat_ids')):
            time.sleep(30)
            continue
        if profile_token != cfg['token'] and set_profile(cfg):
            profile_token = cfg['token']
        try:
            data = _api('getUpdates', {'offset': offset, 'timeout': 25}, cfg['token'])
        except Exception:
            time.sleep(10)
            continue
        if not data.get('ok'):
            time.sleep(15)
            continue
        for update in data.get('result', []):
            offset = max(offset, int(update.get('update_id', 0)) + 1)
            try:
                handle_update(update, cfg)
            except Exception:
                pass


def start():
    """Start the poller + digest threads once per process."""
    global _started
    if os.environ.get('SANADEX_TELEGRAM_DISABLE') == '1':
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_poll_loop, name='sanadex-telegram-poll', daemon=True).start()
    threading.Thread(target=_digest_loop, name='sanadex-telegram-digest', daemon=True).start()
