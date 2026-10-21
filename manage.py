# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - command line maintenance tool.

Examples
--------
    python manage.py info
    python manage.py reset-admin                 # prompts, or generates a password
    python manage.py reset-admin --password NewPass123 --username admin
    python manage.py create-admin owner --password OwnerPass1
    python manage.py backup
    python manage.py restore backups/sanadex-backup-....db
    python manage.py license
    python manage.py check

Every command prints the database file it used, so you always know which data
you are touching (the app resolves it the same way: SANADEX_DB_PATH or
sanadex.db next to app.py).
"""
import argparse
import getpass
import os
import string
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import db  # noqa: E402
import license as license_lib  # noqa: E402
from framework import APP_NAME, hash_password  # noqa: E402


def _open(db_path=None, backup_dir=None, upload_dir=None):
    if db_path:
        db.DB_PATH = os.path.abspath(db_path)
    if backup_dir:
        db.BACKUP_DIR = os.path.abspath(backup_dir)
    if upload_dir:
        db.UPLOAD_DIR = os.path.abspath(upload_dir)
    db.init_db()
    return db.connect()


def _random_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(alphabet[int.from_bytes(os.urandom(1), 'big') % len(alphabet)]
                   for _ in range(length))


def _ask_password(label):
    try:
        first = getpass.getpass(label)
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled.')
        sys.exit(1)
    if not first:
        generated = _random_password()
        print(f'No password entered — generated one: {generated}')
        return generated
    try:
        second = getpass.getpass('Repeat: ')
    except (EOFError, KeyboardInterrupt):
        print('\nCancelled.')
        sys.exit(1)
    if first != second:
        print('Passwords do not match.')
        sys.exit(1)
    if len(first) < 6:
        print('Use at least 6 characters.')
        sys.exit(1)
    return first


def _banner(conn):
    print(f'{APP_NAME}')
    print(f'  database : {db.DB_PATH}')
    print(f'  engine   : {"Microsoft SQL Server" if db.DIALECT == "mssql" else "SQLite"}')
    print(f'  backups  : {db.BACKUP_DIR}')
    print(f'  uploads  : {db.UPLOAD_DIR}')
    try:
        print(f'  version  : schema {db.SCHEMA_VERSION}')
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_info(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    _banner(conn)
    print('\nUsers:')
    users = db.q(conn, 'SELECT id, username, full_name, active, is_admin, role_id, last_login FROM users ORDER BY id')
    if not users:
        print('  (none)')
    for user in users:
        last = time.strftime('%Y-%m-%d %H:%M', time.localtime(user['last_login'])) if user['last_login'] else 'never'
        flags = []
        if user['is_admin']:
            flags.append('admin')
        if not user['active']:
            flags.append('DISABLED')
        print(f"  #{user['id']:<3} {user['username']:<18} {user['full_name'] or '':<22} "
              f"role={user['role_id'] or '-':<4} last={last:<16} {' '.join(flags)}")
    print('\nData:')
    for table in ('items', 'customers', 'suppliers', 'invoices', 'doc_orders', 'vouchers',
                  'treasuries', 'stores', 'employees', 'journals'):
        try:
            print(f"  {table:<12} {db.scalar(conn, f'SELECT COUNT(*) c FROM {table}'):>8}")
        except Exception as exc:
            print(f'  {table:<12} n/a ({exc})')
    state = license_lib.state(conn)
    print(f"\nLicence: {state['state']} ({state['edition']}, expires {state['expires'] or '-'})")
    print('\nLogin hint: if you cannot sign in as admin run  python manage.py reset-admin')
    conn.close()


def _ensure_admin(conn, username, password, full_name='Administrator'):
    username = (username or 'admin').strip()
    existing = db.one(conn, 'SELECT id FROM users WHERE username=?', (username,))
    hashed = hash_password(password)
    if existing:
        db.ex(conn, 'UPDATE users SET password=?, active=1, is_admin=1 WHERE id=?',
              (hashed, existing['id']))
        print(f'Password updated for "{username}" (id {existing["id"]}) and the account was re-enabled.')
        return existing['id']
    role = db.one(conn, 'SELECT id FROM roles ORDER BY id LIMIT 1')
    db.ex(conn, 'INSERT INTO users(username,password,full_name,role_id,is_admin,active,created_at) '
                'VALUES(?,?,?,?,?,?,?)',
          (username, hashed, full_name, role['id'] if role else 0, 1, 1, time.time()))
    new_id = db.scalar(conn, 'SELECT id FROM users WHERE username=?', (username,))
    print(f'Created admin user "{username}" (id {new_id}).')
    return new_id


def cmd_reset_admin(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    _banner(conn)
    password = args.password or _ask_password(f'New password for "{args.username}": ')
    _ensure_admin(conn, args.username, password, args.full_name)
    print('\nDone. Sign in with:')
    print(f'  username: {args.username}')
    print(f'  password: {password}')
    print('Change it later from Users inside the application.')
    conn.close()


def cmd_create_admin(args):
    args.password = args.password or _ask_password(f'Password for new admin "{args.username}": ')
    cmd_reset_admin(args)
    print('\nNote: the built-in administrator is a normal Users record — manage it from the Users screen too.')


def cmd_list_users(args):
    args.db = args.db
    cmd_info(args)


def cmd_backup(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    conn.close()
    result = db.backup_now(upload_to_drive=False)
    print(f'Backup created: {result["path"]}')
    print(f'  size: {result["bytes"] / 1024:.1f} KB')
    print(f'  available backups: {len(db.backup_files())}')


def cmd_restore(args):
    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print(f'File not found: {path}')
        sys.exit(1)
    _open(args.db, args.backup_dir, args.upload_dir)
    try:
        db.restore_backup(path)
        print(f'Restored {path} into {db.DB_PATH}')
        print('Restart the application if it is running.')
    except Exception as exc:
        print(f'Restore failed: {exc}')
        sys.exit(1)


def cmd_license(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    _banner(conn)
    state = license_lib.state(conn)
    print('\nLicence:')
    for key in ('state', 'edition', 'customer', 'seats', 'expires', 'days_left', 'machine'):
        print(f'  {key:<10} {state.get(key)}')
    print(f'  this machine id: {license_lib.machine_id()}')
    if args.generate:
        key = license_lib.generate(args.customer or 'Licensed customer', args.edition,
                                   args.seats, args.days, args.expires, args.machine or '')
        print(f'\nGenerated key:\n{key}')
    if args.activate:
        result = license_lib.activate(conn, args.activate, 'cli', allow_machine_mismatch=True)
        print('\nActivation:', 'ok' if result['ok'] else result['reason'])
    conn.close()


def cmd_check(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    _banner(conn)
    ok = True
    try:
        if conn.dialect == 'sqlite':
            integrity = db.scalar(conn, 'PRAGMA integrity_check', (), 'unknown')
            print(f'  integrity_check: {integrity}')
            ok = integrity == 'ok'
            missing = []
            for table, columns in db.TABLES.items():
                have = db.existing_columns(conn, table)
                if not have:
                    missing.append(f'{table} (table missing)')
                    continue
                for column in columns:
                    if column[0] not in have:
                        missing.append(f'{table}.{column[0]}')
            print(f'  missing columns: {len(missing)}')
            for item in missing[:20]:
                print(f'    - {item}')
            ok = ok and not missing
        users = db.scalar(conn, 'SELECT COUNT(*) c FROM users WHERE is_admin=1 AND active=1')
        print(f'  active admins  : {users}')
        if not users:
            print('  -> no active admin: run  python manage.py reset-admin --username admin')
            ok = False
    except Exception as exc:
        print(f'  check failed: {exc}')
        ok = False
    print('\nResult:', 'OK' if ok else 'PROBLEMS FOUND')
    conn.close()
    sys.exit(0 if ok else 1)


def cmd_init(args):
    conn = _open(args.db, args.backup_dir, args.upload_dir)
    _banner(conn)
    admin = db.one(conn, "SELECT id FROM users WHERE username='admin'")
    if not admin:
        _ensure_admin(conn, 'admin', 'admin123')
        print('\nDefault login created: admin / admin123 (change it from Users).')
    else:
        print('\nDatabase ready. If sign-in fails: python manage.py reset-admin')
    conn.close()


# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(description=f'{APP_NAME} maintenance tool')
    parser.add_argument('--db', help='path to the database file (SQLite)')
    parser.add_argument('--backup-dir', help='override the backups folder')
    parser.add_argument('--upload-dir', help='override the uploads folder')
    sub = parser.add_subparsers(dest='command')

    sub.add_parser('info', help='show paths, users and record counts').set_defaults(func=cmd_info)

    reset = sub.add_parser('reset-admin', help='set (or reset) an administrator password')
    reset.add_argument('--username', default='admin')
    reset.add_argument('--password', help='new password (omit to be prompted)')
    reset.add_argument('--full-name', default='Administrator')
    reset.set_defaults(func=cmd_reset_admin)

    create = sub.add_parser('create-admin', help='create another administrator')
    create.add_argument('username')
    create.add_argument('--password')
    create.add_argument('--full-name', default='Administrator')
    create.set_defaults(func=cmd_create_admin)

    sub.add_parser('users', help='list users').set_defaults(func=cmd_list_users)
    sub.add_parser('backup', help='create a backup now').set_defaults(func=cmd_backup)

    restore = sub.add_parser('restore', help='restore a backup file')
    restore.add_argument('file')
    restore.set_defaults(func=cmd_restore)

    lic = sub.add_parser('license', help='show licence state / generate or activate a key')
    lic.add_argument('--generate', action='store_true')
    lic.add_argument('--customer', default='')
    lic.add_argument('--edition', default='standard')
    lic.add_argument('--seats', type=int, default=1)
    lic.add_argument('--days', type=int, default=365)
    lic.add_argument('--expires', default='')
    lic.add_argument('--machine', default='')
    lic.add_argument('--activate', default='', help='licence key to activate')
    lic.set_defaults(func=cmd_license)

    sub.add_parser('check', help='verify the database integrity').set_defaults(func=cmd_check)
    sub.add_parser('init', help='create/upgrade the database and default admin').set_defaults(func=cmd_init)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        print('\nTip: python manage.py info')
        return
    args.func(args)


if __name__ == '__main__':
    main()
