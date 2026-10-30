# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - micro WSGI framework (standard library only).

Tiny routing / request / response layer so the whole product runs on a bare
Python install (no Flask, no pip, no internet).
"""
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
import traceback
import urllib.parse

APP_NAME = 'Sanadex.wanis.online'
SECRET = os.environ.get('SANADEX_SECRET', 'sanadex-wanis-online-cookie-key')
COOKIE = 'sanadex_session'
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 14


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def b64e(b):
    return base64.urlsafe_b64encode(b).decode()


def b64d(s):
    return base64.urlsafe_b64decode(s.encode())


def sign(data):
    return hmac.new(SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()


def hash_password(pw, salt=None):
    salt = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 60000)
    return salt + '$' + dk.hex()


def check_password(pw, stored):
    try:
        salt, _ = stored.split('$', 1)
        return hmac.compare_digest(hash_password(pw, salt), stored)
    except Exception:
        return False


def h(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


# --------------------------------------------------------------------------- #
# request
# --------------------------------------------------------------------------- #
class Request:
    def __init__(self, environ):
        self.environ = environ
        self.method = environ.get('REQUEST_METHOD', 'GET').upper()
        self.path = urllib.parse.unquote(environ.get('PATH_INFO', '/') or '/')
        self.query = {}
        for k, v in urllib.parse.parse_qsl(environ.get('QUERY_STRING', ''), keep_blank_values=True):
            self.query.setdefault(k, []).append(v)
        self.form = {}
        self.files = {}
        self.cookies = {}
        try:
            from http import cookies as _c
            jar = _c.SimpleCookie(environ.get('HTTP_COOKIE', ''))
            for k, m in jar.items():
                self.cookies[k] = m.value
        except Exception:
            pass
        self.session = self._load_session()
        self.user = None
        self.db = None
        self.lang = 'ar'
        if self.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            self._parse_body()

    # ---- input accessors ------------------------------------------------ #
    def q(self, name, default=None):
        v = self.query.get(name)
        return v[0] if v else default

    def qlist(self, name):
        return list(self.query.get(name) or [])

    def f(self, name, default=''):
        v = self.form.get(name)
        if v is None:
            return default
        return v[0] if isinstance(v, list) else v

    def flist(self, name):
        v = self.form.get(name)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def i(self, name, default=0):
        try:
            return int(float(self.f(name) or default))
        except (TypeError, ValueError):
            return default

    def fl(self, name, default=0.0):
        try:
            return float(self.f(name) or default)
        except (TypeError, ValueError):
            return default

    def has(self, name):
        return name in self.form

    # ---- body parsing --------------------------------------------------- #
    def _parse_body(self):
        try:
            length = int(self.environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            length = 0
        body = self.environ['wsgi.input'].read(length) if length > 0 else b''
        ctype = self.environ.get('CONTENT_TYPE', '') or ''
        if ctype.startswith('multipart/form-data'):
            self._parse_multipart(body, ctype)
        elif ctype.startswith('application/json'):
            try:
                data = json.loads(body.decode('utf-8', 'replace') or '{}')
            except Exception:
                data = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    self.form[k] = v if isinstance(v, list) else [v]
        else:
            for k, v in urllib.parse.parse_qsl(body.decode('utf-8', 'replace'), keep_blank_values=True):
                self.form.setdefault(k, []).append(v)

    def _parse_multipart(self, body, ctype):
        m = re.search(r'boundary=([^;]+)', ctype)
        if not m:
            return
        boundary = m.group(1).strip().strip('"')
        delim = ('--' + boundary).encode()
        for chunk in body.split(delim):
            if not chunk or chunk in (b'--', b'--\r\n', b'\r\n'):
                continue
            if chunk.startswith(b'\r\n'):
                chunk = chunk[2:]
            if chunk.endswith(b'\r\n'):
                chunk = chunk[:-2]
            head, sep, content = chunk.partition(b'\r\n\r\n')
            if not sep:
                continue
            headers = head.decode('utf-8', 'replace')
            fm = re.search(r'filename="([^"]*)"', headers)
            nm = re.search(r'\bname="([^"]+)"', headers)
            if not nm:
                continue
            name = nm.group(1)
            if fm and fm.group(1):
                self.files[name] = {'filename': os.path.basename(fm.group(1)), 'data': content}
            else:
                self.form.setdefault(name, []).append(content.decode('utf-8', 'replace'))

    # ---- session -------------------------------------------------------- #
    def _load_session(self):
        raw = self.cookies.get(COOKIE)
        if not raw or '.' not in raw:
            return {}
        data, sig = raw.rsplit('.', 1)
        if not hmac.compare_digest(sign(data), sig):
            return {}
        try:
            val = json.loads(b64d(data))
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    def is_https(self):
        """True when the browser reached us over HTTPS (directly or via proxy)."""
        forced = str(os.environ.get('SANADEX_FORCE_HTTPS') or '').strip().lower()
        if forced in ('1', 'true', 'yes', 'on'):
            return True
        proto = (self.environ.get('HTTP_X_FORWARDED_PROTO') or '').split(',')[0].strip().lower()
        return (proto == 'https' or self.environ.get('wsgi.url_scheme') == 'https'
                or str(self.environ.get('HTTPS') or '').lower() in ('on', '1'))

    def session_cookie(self):
        data = b64e(json.dumps(self.session, ensure_ascii=False).encode())
        secure = '; Secure' if self.is_https() else ''
        return ('Set-Cookie',
                f'{COOKIE}={data}.{sign(data)}; Path=/; HttpOnly; SameSite=Lax{secure}; '
                f'Max-Age={SESSION_COOKIE_MAX_AGE}')


# --------------------------------------------------------------------------- #
# response
# --------------------------------------------------------------------------- #
class Response:
    def __init__(self, body=b'', status='200 OK', headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.body = body
        self.status = status
        self.headers = list(headers or [('Content-Type', 'text/html; charset=utf-8')])

    def set_cookie_header(self, req):
        self.headers = [x for x in self.headers if x[0].lower() != 'set-cookie']
        self.headers.append(req.session_cookie())
        return self


def html(body, status='200 OK'):
    return Response(body, status)


def json_response(obj, status='200 OK'):
    return Response(json.dumps(obj, ensure_ascii=False, default=str), status,
                    [('Content-Type', 'application/json; charset=utf-8')])


def redirect(loc, req=None):
    r = Response(b'', '302 Found', [('Location', loc)])
    if req is not None:
        r.set_cookie_header(req)
    return r


def file_response(data, filename, ctype='application/octet-stream', inline=False):
    disp = 'inline' if inline else 'attachment'
    safe = filename.replace('"', '')
    return Response(data, '200 OK', [
        ('Content-Type', ctype),
        ('Content-Disposition', f'{disp}; filename="{safe}"; filename*=UTF-8\'\'{urllib.parse.quote(safe)}'),
        ('Cache-Control', 'no-store'),
    ])


def not_found(message=None):
    return Response(message or '<!doctype html><meta charset="utf-8"><title>404</title>'
                    '<div style="font:15px system-ui;padding:40px">404 &mdash; Not found / '
                    'الصفحة غير موجودة</div>', '404 Not Found')


def forbidden(message=None):
    return Response(message or '<!doctype html><meta charset="utf-8"><title>403</title>'
                    '<div style="font:15px system-ui;padding:40px">403 &mdash; Forbidden / '
                    'غير مصرح لك بالوصول</div>', '403 Forbidden')


def static_response(path, max_age=3600):
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError:
        return not_found()
    ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    if path.endswith('.js'):
        ctype = 'application/javascript; charset=utf-8'
    if path.endswith('.css'):
        ctype = 'text/css; charset=utf-8'
    return Response(data, '200 OK', [('Content-Type', ctype),
                                     ('Cache-Control', f'public, max-age={max_age}')])


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #
class Router:
    def __init__(self):
        self.routes = []

    def route(self, name, pattern, methods=('GET',)):
        def deco(fn):
            regex = re.compile('^' + re.sub(r'<(\w+)>', r'(?P<\1>[^/]+)', pattern) + '$')
            self.routes.append((name, tuple(methods), regex, pattern, fn))
            return fn
        return deco

    def match(self, method, path):
        path = path.rstrip('/') or '/'
        for name, methods, regex, pattern, fn in self.routes:
            m = regex.match(path)
            if m and method in methods:
                return fn, m.groupdict(), name, pattern
        return None, None, None, None

    def url_for(self, route_name, **kw):
        for n, methods, regex, pattern, fn in self.routes:
            if n == route_name:
                def rep(m):
                    key = m.group(1)
                    return urllib.parse.quote(str(kw.pop(key, '')))
                return re.sub(r'<(\w+)>', rep, pattern)
        return '#'

    def names(self):
        return [r[0] for r in self.routes]


router = Router()
url_for = router.url_for


class StaticFiles:
    """Minimal static handler; mounts /static/<file>."""

    def __init__(self, directory, url_prefix='/static/'):
        self.directory = directory
        self.url_prefix = url_prefix

    def handles(self, path):
        return path.startswith(self.url_prefix)

    def serve(self, path):
        name = path[len(self.url_prefix):]
        safe = os.path.normpath(os.path.join(self.directory, name))
        if not safe.startswith(os.path.abspath(self.directory)):
            return not_found()
        return static_response(safe)


def read_upload(upload, folder, prefix, allowed=('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico')):
    """Persist an uploaded file and return the stored filename (or None)."""
    if not upload or not upload.get('data'):
        return None
    ext = os.path.splitext(upload['filename'])[1].lower()[:10]
    if allowed and ext not in allowed:
        return None
    name = f'{prefix}_{int(time.time() * 1000)}{ext}'
    try:
        with open(os.path.join(folder, name), 'wb') as fh:
            fh.write(upload['data'])
    except OSError:
        return None
    return name


def log_exception():
    return traceback.format_exc()
