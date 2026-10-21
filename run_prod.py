# -*- coding: utf-8 -*-
"""Production launcher for Sanadex.wanis.online.

Uses waitress (multi-threaded, pure Python) when it is installed, otherwise it
falls back to a threaded standard-library WSGI server so the app still runs
without extra packages.

    pip install waitress
    python run_prod.py

Environment:
    HOST=0.0.0.0            bind address (use 127.0.0.1 behind nginx/Caddy)
    PORT=8000               port
    WEB_THREADS=8           waitress worker threads
    SANADEX_SECRET=...      session signing key (set it in production!)
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import db  # noqa: E402
import app as app_module  # noqa: E402


def main():
    db.init_db()
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '8000'))
    threads = int(os.environ.get('WEB_THREADS', '8'))
    if not os.environ.get('SANADEX_SECRET'):
        print('WARNING: SANADEX_SECRET is not set — sessions use the built-in key.')
        print('         Set it before exposing the app to a network.')
    engine = 'Microsoft SQL Server' if db.DIALECT == 'mssql' else 'SQLite'
    print(f'Sanadex.wanis.online — {engine}')
    print(f'  database: {db.DB_PATH}')
    print(f'  listening: http://{host}:{port}')
    try:
        from waitress import serve
        print(f'Serving with waitress ({threads} threads)')
        serve(app_module.application, host=host, port=port, threads=threads,
              ident='Sanadex', clear_untrusted_proxy_headers=True,
              max_request_body_size=64 * 1024 * 1024)
    except ImportError:
        print('waitress is not installed — using the threaded stdlib server instead.')
        print('Install it with:  pip install waitress')
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

        class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True
            allow_reuse_address = True

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        try:
            make_server(host, port, app_module.application, server_class=ThreadingWSGIServer,
                        handler_class=QuietHandler).serve_forever()
        except KeyboardInterrupt:
            print('\nStopped.')


if __name__ == '__main__':
    main()
