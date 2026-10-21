# -*- coding: utf-8 -*-
"""Sanadex.wanis.online - Google Drive backup sync.

Uses the OAuth "installed app" flow (authorization code -> refresh token) so the
product can push backups to the user's own Drive without shipping any secret.
Only Python's standard library is used; every call degrades gracefully when the
machine is offline.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DRIVE_FILES = 'https://www.googleapis.com/drive/v3/files'
DRIVE_UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
SCOPE = 'https://www.googleapis.com/auth/drive.file'
TIMEOUT = 30
FOLDER_MIME = 'application/vnd.google-apps.folder'


class DriveError(RuntimeError):
    pass


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read()[:800]
        raise DriveError(f'HTTP {exc.code}: {body.decode("utf-8", "replace")}')
    except urllib.error.URLError as exc:
        raise DriveError(f'Network error: {exc.reason}')
    except Exception as exc:  # pragma: no cover - defensive
        raise DriveError(str(exc))


def authorize_url(client_id, redirect_uri, state='sanadex'):
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    }
    return AUTH_URL + '?' + urllib.parse.urlencode(params)


def _post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode()
    status, raw = _request(url, data=body, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
    })
    try:
        return json.loads(raw.decode('utf-8', 'replace'))
    except Exception:
        raise DriveError('Invalid response from Google')


def exchange_code(code, client_id, client_secret, redirect_uri):
    data = _post_form(TOKEN_URL, {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    })
    if 'refresh_token' not in data:
        raise DriveError('Google did not return a refresh token (try again with prompt=consent).')
    return data


def access_token(client_id, client_secret, refresh_token):
    if not (client_id and client_secret and refresh_token):
        raise DriveError('Google Drive is not connected yet.')
    data = _post_form(TOKEN_URL, {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    })
    token = data.get('access_token')
    if not token:
        raise DriveError('Could not obtain an access token.')
    return token


def account_email(token):
    try:
        status, raw = _request('https://www.googleapis.com/oauth2/v2/userinfo',
                               headers={'Authorization': f'Bearer {token}'})
        return json.loads(raw.decode('utf-8', 'replace')).get('email', '')
    except Exception:
        return ''


def _multipart(metadata, content, mime='application/octet-stream'):
    boundary = 'sanadex----------------' + os.urandom(8).hex()
    parts = [
        f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode(),
        json.dumps(metadata, ensure_ascii=False).encode('utf-8'),
        f'\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n'.encode(),
        content,
        f'\r\n--{boundary}--\r\n'.encode(),
    ]
    return b''.join(parts), boundary


def upload(path, name, client_id, client_secret, refresh_token, folder_id=''):
    token = access_token(client_id, client_secret, refresh_token)
    with open(path, 'rb') as fh:
        content = fh.read()
    metadata = {'name': name}
    if folder_id:
        metadata['parents'] = [folder_id]
    body, boundary = _multipart(metadata, content)
    url = (f'{DRIVE_UPLOAD}?uploadType=multipart&fields=id,name,size,createdTime,webViewLink'
           f'&supportsAllDrives=true')
    status, raw = _request(url, data=body, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': f'multipart/related; boundary={boundary}',
    })
    info = json.loads(raw.decode('utf-8', 'replace'))
    return {'ok': True, 'id': info.get('id'), 'name': info.get('name'),
            'link': info.get('webViewLink')}


def list_backups(client_id, client_secret, refresh_token, folder_id=''):
    token = access_token(client_id, client_secret, refresh_token)
    query = "name contains 'sanadex-backup' and trashed = false"
    if folder_id:
        query += f" and '{folder_id}' in parents"
    params = urllib.parse.urlencode({
        'q': query, 'orderBy': 'createdTime desc', 'pageSize': 25,
        'fields': 'files(id,name,size,createdTime)',
    })
    status, raw = _request(f'{DRIVE_FILES}?{params}',
                           headers={'Authorization': f'Bearer {token}'})
    return json.loads(raw.decode('utf-8', 'replace')).get('files', [])


def download(file_id, client_id, client_secret, refresh_token):
    token = access_token(client_id, client_secret, refresh_token)
    status, raw = _request(f'{DRIVE_FILES}/{file_id}?alt=media',
                           headers={'Authorization': f'Bearer {token}'})
    return raw


def ensure_folder(client_id, client_secret, refresh_token, name='Sanadex backups'):
    token = access_token(client_id, client_secret, refresh_token)
    query = (f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false")
    params = urllib.parse.urlencode({'q': query, 'fields': 'files(id,name)'})
    status, raw = _request(f'{DRIVE_FILES}?{params}',
                           headers={'Authorization': f'Bearer {token}'})
    files = json.loads(raw.decode('utf-8', 'replace')).get('files', [])
    if files:
        return files[0]['id']
    body = json.dumps({'name': name, 'mimeType': FOLDER_MIME}).encode()
    status, raw = _request(DRIVE_FILES, data=body, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=UTF-8',
    })
    return json.loads(raw.decode('utf-8', 'replace')).get('id')
