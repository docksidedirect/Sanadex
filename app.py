# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - application entry point (WSGI).

Run with:  python app.py        (or run.bat / run.sh)
Then open: http://localhost:8000   admin / admin123
"""
import os
import sys
import threading
import time
import traceback

import core
import db
import license as license_lib
import telegram_bot
from framework import (APP_NAME, Request, Response, not_found, redirect, router,
                       static_response, url_for)

# Importing the view modules registers every route.
import crud  # noqa: F401  (declarative CRUD screens)
import views_hr  # noqa: F401
import views_main  # noqa: F401
import views_money  # noqa: F401
import views_reports  # noqa: F401
import views_sales  # noqa: F401
import views_stock  # noqa: F401

BASE = core.BASE
STATIC_DIR = os.path.join(BASE, 'static')
VERSION = '3.0'
_ready = {'path': None}
_db_lock = threading.Lock()
_last_backup_check = {'ts': 0.0}


def ensure_database():
    if _ready['path'] == db.DB_PATH:
        return
    with _db_lock:
        if _ready['path'] != db.DB_PATH:
            db.init_db()
            _ready['path'] = db.DB_PATH
            telegram_bot.start()


TRIAL_EXEMPT_PREFIXES = ('/static/', '/uploads/', '/lang/', '/theme/', '/signup')
TRIAL_EXEMPT_PATHS = {'/login', '/logout', '/healthz', '/license', '/profile', '/favicon.ico', '/signup'}


def _trial_exempt(path):
    if path in TRIAL_EXEMPT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in TRIAL_EXEMPT_PREFIXES)

def _serve_static(path):
    name = os.path.basename(path)
    if not name or name.startswith('.'):
        return not_found()
    return static_response(os.path.join(STATIC_DIR, name))


def _serve_upload(path):
    name = os.path.basename(path)
    full = os.path.join(db.UPLOAD_DIR, name)
    if not os.path.isfile(full):
        return not_found()
    content_type = 'application/octet-stream'
    lowered = name.lower()
    for ext, ctype in (('.png', 'image/png'), ('.jpg', 'image/jpeg'), ('.jpeg', 'image/jpeg'),
                       ('.gif', 'image/gif'), ('.webp', 'image/webp'), ('.svg', 'image/svg+xml'),
                       ('.ico', 'image/x-icon')):
        if lowered.endswith(ext):
            content_type = ctype
            break
    with open(full, 'rb') as fh:
        return Response(fh.read(), '200 OK', [('Content-Type', content_type),
                                              ('Cache-Control', 'public, max-age=600')])


def _error_page(req, exc):
    detail = ''
    if os.environ.get('SANADEX_DEBUG') and req.user and (req.user or {}).get('is_admin'):
        detail = traceback.format_exc()
    try:
        response = core.render(req, 'error.html', http_status='500 Internal Server Error',
                               title=core.T(req, 'server_error'),
                               message=core.T(req, 'server_error'), code='500', detail=detail)
        response.headers.append(('Cache-Control', 'no-store'))
    except Exception:
        response = Response('<!doctype html><meta charset="utf-8"><h1>500</h1>',
                            '500 Internal Server Error')
    return response


def application(environ, start_response):
    ensure_database()
    req = Request(environ)
    response = None
    conn = None
    try:
        if req.path.startswith('/static/'):
            response = _serve_static(req.path)
        elif req.path.startswith('/uploads/'):
            response = _serve_upload(req.path)
        elif req.path == '/favicon.ico':
            response = _serve_static('/static/favicon.svg')
        else:
            conn = db.connect()
            req.db = conn
            req.company = db.one(conn, 'SELECT * FROM company WHERE id=1') or {}
            user_id = req.session.get('uid')
            if user_id:
                user = db.one(conn, 'SELECT * FROM users WHERE id=? AND active=1', (user_id,))
                if user:
                    user['perms'] = {}
                    if user.get('role_id'):
                        role = db.one(conn, 'SELECT * FROM roles WHERE id=?', (user['role_id'],))
                        if role:
                            user['perms'] = role
                    req.user = user
                else:
                    req.session.pop('uid', None)
            if req.user and not req.user.get('is_admin'):
                if not req.user.get('trial_started'):
                    license_lib.start_trial(conn, req.user['id'])
                    req.user['trial_started'] = time.time()
                trial = license_lib.user_state(conn, req.user, req.company)
                if trial['state'] == 'trial_expired' and not _trial_exempt(req.path):
                    response = core.render(req, 'trial_expired.html',
                                           http_status='403 Forbidden',
                                           title=core.T(req, 'license_trial_expired'),
                                           lic=trial)
            if response is not None:
                pass
            else:
                handler, kwargs, route_name, pattern = router.match(req.method, req.path)
                if req.path not in ('/login', '/healthz', '/signup') \
                        and not req.path.startswith(('/lang/', '/theme/')) and not req.user:
                    response = redirect(url_for('login'), req)
                elif handler is None:
                    response = not_found()
                else:
                    if req.user and time.time() - _last_backup_check['ts'] > 900:
                        _last_backup_check['ts'] = time.time()
                        db.maybe_auto_backup(conn)
                    response = handler(req, **kwargs) if kwargs else handler(req)
                    if response is None:
                        response = not_found()
    except RuntimeError as exc:  # startup guard (missing database) must be visible
        try:
            response = core.render(req, 'error.html', http_status='500 Internal Server Error',
                                   title=core.T(req, 'server_error'), code='500',
                                   message=str(exc).splitlines()[0],
                                   detail=str(exc))
        except Exception:
            response = Response(str(exc), '500 Internal Server Error')
    except Exception as exc:  # pragma: no cover - safety net
        response = _error_page(req, exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not isinstance(response, Response):
        response = Response(str(response))
    response.set_cookie_header(req)
    start_response(response.status, response.headers)
    return [response.body]


def main():
    from wsgiref.simple_server import WSGIRequestHandler, make_server
    db.init_db()
    telegram_bot.start()
    port = int(os.environ.get('PORT', '8000'))
    host = os.environ.get('HOST', '0.0.0.0')

    class Handler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    engine = 'Microsoft SQL Server' if db.DIALECT == 'mssql' else 'SQLite'
    print(f'  {APP_NAME} v{VERSION}')
    print(f'  Engine: {engine}')
    print(f'  Database: {db.DB_PATH}')
    print(f'  Running on http://localhost:{port}')
    print('  Login: admin / admin123  (change it in Users)')
    print('  Cannot sign in? close this window and run: python manage.py reset-admin')
    sys.stdout.flush()
    try:
        make_server(host, port, application, handler_class=Handler).serve_forever()
    except KeyboardInterrupt:
        print('\n  Stopped.')


if __name__ == '__main__':
    main()
