# Deploying Sanadex.wanis.online

This guide covers the four realistic ways to run the product, plus the
checklists that matter for a business system (backups, HTTPS, secrets).

- **A. Windows shop PC / local network** — fastest, no domain needed.
- **B. VPS + domain + HTTPS** (nginx + systemd) — public `sanadex.wanis.online`.
- **C. Docker** — reproducible, any host.
- **D. Cloudflare Tunnel** — public URL with no open ports or static IP.

> Verified commands target Windows 10/11 and Ubuntu 22.04/24.04 with Python 3.10+.
> The app is a plain WSGI application, so any WSGI server (waitress, gunicorn,
> uWSGI) works. `run_prod.py` uses waitress and falls back to the stdlib server.

---

## 0. Before you expose it to anyone

1. **Set a session secret** (`SANADEX_SECRET`). Generate one:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   Without it every install shares the built-in key, which lets anyone forge a
   session cookie if they know the source.
2. **Change the admin password** — do not ship `admin123`. Users → edit `admin`.
3. **Turn on automatic backups** (Settings → backups) and link Google Drive.
4. **Use HTTPS** if the app is reachable from the internet (Option B/C/D).
5. **Keep the database on local disk** (SQLite + WAL over network shares is unsafe).

Environment variables (all optional; defaults in brackets):

| Variable | Meaning |
| --- | --- |
| `SANADEX_SECRET` | session signing key — **set in production** |
| `SANADEX_LICENSE_SECRET` | signs generated licence keys — keep private |
| `SANADEX_FORCE_HTTPS` | `1` behind a TLS proxy — enables Secure cookies + HSTS |
| `SANADEX_DB_PATH` | SQLite file [`sanadex.db` next to `app.py`] |
| `SANADEX_BACKUP_DIR` | backups folder [`backups/`] |
| `SANADEX_UPLOAD_DIR` | logos/uploads folder [`uploads/`] |
| `HOST` / `PORT` | bind address / port [`0.0.0.0` / `8000`] |
| `WEB_THREADS` | waitress worker threads [`8`] |
| `SANADEX_DEBUG` | `1` shows tracebacks to administrators |
| `SANADEX_DB=mssql` + `SANADEX_MSSQL_DSN` | run on Microsoft SQL Server instead of SQLite |
| `SANADEX_TELEGRAM_TOKEN` | Telegram bot token — optional, overrides the Licence screen |
| `SANADEX_TELEGRAM_CHAT_ID` | admin chat id(s), comma-separated — optional, overrides the screen |
| `SANADEX_TELEGRAM_DIGEST_HOUR` | daily digest hour `0–23` [`21`] |
| `SANADEX_PUBLIC_URL` | public app URL used by the bot's “Open admin” button |
| `SANADEX_TELEGRAM_DISABLE` | `1` on every extra WSGI worker/replica except one |

### Coolify (Docker)

Create the application from this repository (Dockerfile or Docker Compose).
Coolify injects the variables below into the container; no `.env` file is needed
on the server. Add them under **Application → Environment Variables**, then
**Redeploy** (environment changes do not need a Git push).

| Variable | Required? | Value |
| --- | --- | --- |
| `SANADEX_SECRET` | **yes** | 48+ random characters |
| `SANADEX_LICENSE_SECRET` | **yes** | private licence-signing secret |
| `SANADEX_ADMIN_PASSWORD` | optional | pin the initial admin password |
| `SANADEX_TELEGRAM_TOKEN` | optional | token from @BotFather |
| `SANADEX_TELEGRAM_CHAT_ID` | optional | your numeric Telegram id |
| `SANADEX_PUBLIC_URL` | optional | `https://your-app-domain` |
| `SANADEX_TELEGRAM_DIGEST_HOUR` | optional | `21` |
| `SANADEX_TELEGRAM_DISABLE` | only with >1 replica | `1` on all but one |

Use **either** the Licence screen **or** the environment variables for the
Telegram token/chat id. Environment variables win, so if you set them in Coolify
the fields on the Licence screen are ignored (this is intentional for secrets).

Mount a persistent volume at **`/data`** (the image already declares it) so
`sanadex.db`, `backups/` and `uploads/` survive redeploys.

The bundled `docker-compose.yml` uses `${VAR}` substitutions and `expose: 8000`
so it works both with Coolify and with a local `docker compose` + `.env` file
(uncomment the `ports:` block for direct local access).


---

## A. Windows PC or shop server (LAN)

### A1. Run it
1. Install Python 3.10+ from python.org (check **Add python.exe to PATH**).
2. Put the folder somewhere stable, e.g. `C:\Sanadex` (not Desktop/Downloads).
3. Double-click **`run-prod.bat`** (creates `.venv`, installs waitress, starts the app) —
   or for a quick test `run.bat`.
4. Open <http://localhost:8000> and sign in.

Console output shows the **database file in use** — keep that line visible, it is
the answer to 90% of "my data disappeared" reports.

### A2. Reach it from phones / another PC on the same Wi-Fi
1. Find the PC's IPv4 address: `ipconfig` → e.g. `192.168.1.20`.
2. Allow the port once (PowerShell as Administrator):
   ```powershell
   New-NetFirewallRule -DisplayName "Sanadex 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
3. On the phone open `http://192.168.1.20:8000`.
4. Give the PC a **static/reserved IP** on the router so the address does not change.

### A3. Keep it always running (Windows service)
Use [NSSM](https://nssm.cc/) (free, no installer):
```powershell
nssm install Sanadex "C:\Sanadex\.venv\Scripts\python.exe" "C:\Sanadex\run_prod.py"
nssm set Sanadex AppDirectory "C:\Sanadex"
nssm set Sanadex AppEnvironmentExtra SANADEX_SECRET=CHANGE-ME HOST=0.0.0.0 PORT=8000
nssm set Sanadex Start SERVICE_AUTO_START
nssm set Sanadex AppStdout "C:\Sanadex\service.log"
nssm set Sanadex AppStderr "C:\Sanadex\service.log"
nssm start Sanadex
```
Service commands: `nssm restart Sanadex`, `nssm stop Sanadex`, `nssm remove Sanadex confirm`.

> Alternative without NSSM: Task Scheduler → "Run whether user is logged on or not"
> → trigger "At startup" → action `run_prod.py` with "Start in" = the app folder.

### A4. Backups on this machine
- Settings → **automatic backup** every 24h (keeps the last 40 files).
- Link Google Drive from the backups screen so a disk failure is not fatal.
- Or copy `backups\*.db` nightly with Task Scheduler to OneDrive/another disk.

---

## B. VPS with domain + HTTPS (Ubuntu)

Assumes a fresh Ubuntu 22.04/24.04, a domain `sanadex.wanis.online`, and a VPS
with a public IPv4.

### B1. DNS
At your domain registrar create an **A record**: `sanadex` → `YOUR.VPS.IP`
(and `AAAA` if you have IPv6). Wait for propagation (`nslookup sanadex.wanis.online`).

### B2. Packages and a service user
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx
sudo adduser --system --group --home /opt/sanadex sanadex
sudo mkdir -p /opt/sanadex/data && sudo chown -R sanadex:sanadex /opt/sanadex
```

### B3. Upload the application
From your PC (PowerShell):
```powershell
scp -r "C:\Sanadex\*" root@YOUR.VPS.IP:/opt/sanadex/
```
Exclude local data if you are migrating deliberately: `sahm.db`, `sanadex.db*`,
`backups/`, `uploads/`.

### B4. Virtualenv + dependencies
```bash
sudo -u sanadex python3 -m venv /opt/sanadex/.venv
sudo -u sanadex /opt/sanadex/.venv/bin/pip install --upgrade pip
sudo -u sanadex /opt/sanadex/.venv/bin/pip install "jinja2>=3.1" "waitress>=3.0"
```

### B5. Configuration
```bash
sudo nano /opt/sanadex/.env
```
```ini
SANADEX_SECRET=paste-the-generated-secret
SANADEX_LICENSE_SECRET=paste-another-secret
SANADEX_DB_PATH=/opt/sanadex/data/sanadex.db
SANADEX_BACKUP_DIR=/opt/sanadex/data/backups
SANADEX_UPLOAD_DIR=/opt/sanadex/data/uploads
HOST=127.0.0.1
PORT=8000
WEB_THREADS=8
```
```bash
sudo chown -R sanadex:sanadex /opt/sanadex && sudo chmod 600 /opt/sanadex/.env
sudo -u sanadex /opt/sanadex/.venv/bin/python /opt/sanadex/manage.py init
```

### B6. systemd service
```bash
sudo cp /opt/sanadex/deploy/sanadex.service /etc/systemd/system/sanadex.service
sudo systemctl daemon-reload
sudo systemctl enable --now sanadex
systemctl status sanadex --no-pager
```
The provided unit already points at `/opt/sanadex`, user `sanadex`, port 8000.

### B7. nginx + TLS
```bash
sudo cp /opt/sanadex/deploy/nginx.conf /etc/nginx/sites-available/sanadex
sudo ln -s /etc/nginx/sites-available/sanadex /etc/nginx/sites-enabled/sanadex
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d sanadex.wanis.online
```
Certbot rewrites the server block for HTTPS and installs the renewal timer.

### B8. Firewall
```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```
Do **not** open 8000 publicly; it must stay bound to `127.0.0.1`.

### B9. Verify
```bash
curl -fsS https://sanadex.wanis.online/healthz
# {"ok": true, "app": "Sanadex.wanis.online", ...}
```
Then sign in, change the admin password, and turn on automatic backups.

---

## C. Docker

```bash
cp .env.example .env
# edit .env: SANADEX_SECRET and SANADEX_LICENSE_SECRET at minimum
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
```
- Data lives in `./data` (`sanadex.db`, `backups/`, `uploads/`) — backed up by
  copying that folder.
- The container publishes only `127.0.0.1:8000`; put nginx/Caddy in front for TLS
  (use `deploy/nginx.conf` or `deploy/Caddyfile`).
- Review `Dockerfile` (non-root user, healthcheck, `/data` volume).
- Upgrade: `docker compose down && git pull/copy files && docker compose up -d --build`
  — the database is preserved in `./data`.

---

## D. Cloudflare Tunnel (public URL without opening ports)

Good for a shop PC behind CGNAT, or to give `sanadex.wanis.online` to a few
remote staff without a static IP.

1. Install cloudflared on the machine running Sanadex.
2. Quick test (random URL):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Stable domain:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create sanadex
   cloudflared tunnel route dns sanadex sanadex.wanis.online
   cloudflared tunnel run --url http://localhost:8000 sanadex
   ```
4. Install it as a service (`cloudflared service install`) so it starts with Windows.
5. In Cloudflare, add an Access policy (email OTP) for extra protection.

Because TLS terminates at Cloudflare, the app stays on `localhost` — keep
`HOST=127.0.0.1` and leave the firewall closed.

---

## 5. Backups on a server

The app writes `backups/sanadex-backup-YYYYMMDD-HHMMSS.db` (SQLite backup API, safe
while users are working) and keeps the last 40. Two extra layers:

```bash
# nightly copy off the machine (systemd timer or cron)
0 2 * * * cp -r /opt/sanadex/data/backups /var/backups/sanadex-$(date +\%F) 2>/dev/null
```
and Google Drive from Settings → backups (needs your OAuth client — see README).

Restore from the command line:
```bash
sudo systemctl stop sanadex
sudo -u sanadex /opt/sanadex/.venv/bin/python /opt/sanadex/manage.py restore \
     /opt/sanadex/data/backups/sanadex-backup-20261021-020000.db
sudo systemctl start sanadex
```

Test a restore **before** you need it.

---

## 6. Upgrade procedure

1. Take a manual backup (Settings → backups, or `manage.py backup`).
2. Stop the service (`systemctl stop sanadex`, `docker compose down`, or close the console).
3. Replace the application files — **keep** `sanadex.db*`, `backups/`, `uploads/`, `.env`.
4. Start the service. The database is upgraded automatically (columns are added,
   nothing is dropped).
5. Reload the page, sign in, check Reports → P&L and a recent invoice.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| **"اسم المستخدم أو كلمة المرور غير صحيحة"** while typing `admin123` | Usually the browser autofilling an old saved password (the app used to run on the same `localhost:8000`). Clear the password box, type it by hand — or run `python manage.py reset-admin` and use the password it prints. |
| Locked out, no password | `python manage.py reset-admin` → prints the database path, sets a new password, re-enables the account. `python manage.py info` shows users and which database is in use. |
| `Address already in use` / port 8000 busy | Another instance is running: `netstat -ano \| findstr :8000` then stop that PID (Task Manager, `stop-process -Id N`, `systemctl stop sanadex`). |
| Login works but data looks empty | You are on a different database. The startup banner prints `Database: ...`; compare with `python manage.py info`. Move/copy the intended `sanadex.db` there. |
| 500 errors | Restart with `SANADEX_DEBUG=1` → the error page shows the traceback to admins. Check that `backups/` and `uploads/` are writable. |
| `database is locked` | Two app instances, or the DB sits on OneDrive/network share. Keep it on a local disk and run one instance (waitress threads are fine). |
| Slow first page after start | The automatic backup is running (24h schedule). Check `backups/`. |
| Google Drive connect fails | Redirect URI must match exactly (`http://localhost:8000/gdrive/callback`), Drive API enabled, client secret pasted, machine online. |
| SQL Server mode errors | `pip install pyodbc`, install "ODBC Driver 18", check `SANADEX_MSSQL_DSN`, ensure the login can create tables. |
| HTTPS mixed content / wrong links | Behind a proxy, forward `X-Forwarded-Proto` (both sample configs do). |

Health probe: `GET /healthz` returns JSON (no auth) — wire it into UptimeRobot,
Docker healthcheck, or nginx monitoring.

---

## 8. Final checklist

- [ ] `SANADEX_SECRET` set, app restarted
- [ ] admin password changed (and a second admin account created)
- [ ] HTTPS enabled, HTTP → HTTPS redirect
- [ ] port 8000 not reachable from the internet
- [ ] automatic backups on + Google Drive linked + **one restore tested**
- [ ] `/healthz` monitored
- [ ] database file on local disk, excluded from any sync tool
- [ ] users created per employee (no shared logins), roles reviewed
- [ ] licence generated for the customer (Admin → Licence) and activated
