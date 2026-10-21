# دليل نشر Sanadex.wanis.online

هذا الدليل يغطي الطرق العملية لتشغيل النظام، مع قوائم التحقق المهمة لنظام محاسبي
(النسخ الاحتياطي، HTTPS، الأسرار، كلمة مرور المدير).

- **أ. جهاز ويندوز داخل المحل / الشبكة المحلية** — الأسرع، بدون دومين.
- **ب. سيرفر VPS + دومين + HTTPS** (nginx + systemd) — الموقع العام `sanadex.wanis.online`.
- **ج. Docker** — تشغيل قابل للتكرار على أي سيرفر.
- **د. Cloudflare Tunnel** — رابط عام بدون فتح منافذ أو IP ثابت.

---

## ٠. قبل أن تفتح النظام لأي شخص

1. **مفتاح الجلسات** `SANADEX_SECRET` — أنشئه بأمر:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. **غيّر كلمة مرور المدير** من شاشة المستخدمين — لا تترك `admin123`.
3. **شغّل النسخ الاحتياطي التلقائي** واربط Google Drive من شاشة النسخ الاحتياطي.
4. **استخدم HTTPS** إذا كان النظام متاحاً على الإنترنت.
5. **قاعدة البيانات على قرص محلي** (SQLite + WAL لا يعمل بشكل آمن على مجلد مشترك/OneDrive).

المتغيرات (كلها اختيارية، والقيم الافتراضية بين الأقواس):

| المتغير | المعنى |
| --- | --- |
| `SANADEX_SECRET` | مفتاح توقيع الجلسات — **ضعه في الإنتاج** |
| `SANADEX_LICENSE_SECRET` | يوقّع مفاتيح الترخيص — احتفظ به خاصاً |
| `SANADEX_DB_PATH` | ملف SQLite [`sanadex.db` بجانب `app.py`] |
| `SANADEX_BACKUP_DIR` | مجلد النسخ [`backups/`] |
| `SANADEX_UPLOAD_DIR` | مجلد الشعارات والمرفقات [`uploads/`] |
| `HOST` / `PORT` | عنوان الاستماع والمنفذ [`0.0.0.0` / `8000`] |
| `WEB_THREADS` | عدد خيوط waitress [`8`] |
| `SANADEX_DEBUG` | `1` يعرض تفاصيل الأخطاء للمدير |
| `SANADEX_DB=mssql` + `SANADEX_MSSQL_DSN` | التشغيل على SQL Server بدلاً من SQLite |

---

## أ. جهاز ويندوز في المحل (شبكة محلية)

### أ١. التشغيل
1. ثبّت Python 3.10+ من python.org (اختر **Add python.exe to PATH**).
2. ضع المجلد في مسار ثابت مثل `C:\Sanadex` (ليس Desktop أو Downloads).
3. شغّل **`run-prod.bat`** (ينشئ بيئة افتراضية ويثبّت waitress ويبدأ النظام)،
   أو `run.bat` للتجربة السريعة.
4. افتح <http://localhost:8000> وسجّل الدخول.

نافذة التشغيل تطبع **مسار قاعدة البيانات المستخدمة** — هذه أهم معلومة عند أي
مشكلة في الدخول أو اختفاء البيانات.

### أ٢. الوصول من الموبايل أو جهاز آخر على نفس الشبكة
1. اعرف عنوان الجهاز: `ipconfig` → مثلاً `192.168.1.20`.
2. افتح المنفذ مرة واحدة (PowerShell كمسؤول):
   ```powershell
   New-NetFirewallRule -DisplayName "Sanadex 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
3. من الموبايل: `http://192.168.1.20:8000`.
4. ثبّت IP ثابتاً للجهاز من الراوتر حتى لا يتغير العنوان.

### أ٣. تشغيل دائم كخدمة ويندوز
استخدم [NSSM](https://nssm.cc/) المجاني:
```powershell
nssm install Sanadex "C:\Sanadex\.venv\Scripts\python.exe" "C:\Sanadex\run_prod.py"
nssm set Sanadex AppDirectory "C:\Sanadex"
nssm set Sanadex AppEnvironmentExtra SANADEX_SECRET=ضع-المفتاح HOST=0.0.0.0 PORT=8000
nssm set Sanadex Start SERVICE_AUTO_START
nssm start Sanadex
```
أوامر الخدمة: `nssm restart Sanadex` / `nssm stop Sanadex` / `nssm remove Sanadex confirm`.

> بديل بدون NSSM: Task Scheduler → "Run whether user is logged on or not" →
> عند بدء التشغيل → شغّل `run_prod.py` مع "Start in" = مجلد البرنامج.

### أ٤. النسخ الاحتياطي على هذا الجهاز
- الإعدادات ← نسخ احتياطي تلقائي كل ٢٤ ساعة (يحتفظ بآخر ٤٠ نسخة).
- اربط Google Drive من شاشة النسخ الاحتياطي حتى لا تكون كارثة القرص نهاية البيانات.
- أو انسخ مجلد `backups` ليلياً إلى OneDrive/قرص آخر عبر Task Scheduler.

---

## ب. سيرفر VPS + دومين + HTTPS

### ب١. الدومين
من مزوّد الدومين أضف سجل **A**: `sanadex` → `IP السيرفر`.
(وسجل `AAAA` إن كان لديك IPv6.) تحقق بـ `nslookup sanadex.wanis.online`.

### ب٢. الحزم ومستخدم الخدمة
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx
sudo adduser --system --group --home /opt/sanadex sanadex
sudo mkdir -p /opt/sanadex/data && sudo chown -R sanadex:sanadex /opt/sanadex
```

### ب٣. رفع الملفات
من جهازك (PowerShell):
```powershell
scp -r "C:\Sanadex\*" root@IP-السيرفر:/opt/sanadex/
```
لا ترفع `sahm.db` / `sanadex.db*` / `backups/` / `uploads/` إلا إذا كنت تنقل
البيانات فعلاً.

### ب٤. البيئة الافتراضية والمكتبات
```bash
sudo -u sanadex python3 -m venv /opt/sanadex/.venv
sudo -u sanadex /opt/sanadex/.venv/bin/pip install --upgrade pip
sudo -u sanadex /opt/sanadex/.venv/bin/pip install "jinja2>=3.1" "waitress>=3.0"
```

### ب٥. الإعدادات
```bash
sudo nano /opt/sanadex/.env
```
```ini
SANADEX_SECRET=المفتاح-المولّد
SANADEX_LICENSE_SECRET=مفتاح-آخر-للترخيص
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

### ب٦. خدمة systemd
```bash
sudo cp /opt/sanadex/deploy/sanadex.service /etc/systemd/system/sanadex.service
sudo systemctl daemon-reload
sudo systemctl enable --now sanadex
systemctl status sanadex --no-pager
```

### ب٧. nginx وشهادة SSL
```bash
sudo cp /opt/sanadex/deploy/nginx.conf /etc/nginx/sites-available/sanadex
sudo ln -s /etc/nginx/sites-available/sanadex /etc/nginx/sites-enabled/sanadex
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d sanadex.wanis.online
```

### ب٨. الجدار الناري
```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```
**لا تفتح المنفذ 8000 للعالم** — يجب أن يبقى على `127.0.0.1` فقط.

### ب٩. التحقق
```bash
curl -fsS https://sanadex.wanis.online/healthz
# {"ok": true, "app": "Sanadex.wanis.online", ...}
```
ثم سجّل الدخول، غيّر كلمة مرور المدير، وشغّل النسخ الاحتياطي التلقائي.

---

## ج. Docker

```bash
cp .env.example .env
# عدّل .env: SANADEX_SECRET و SANADEX_LICENSE_SECRET على الأقل
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
```
- البيانات في مجلد `./data` (`sanadex.db` و`backups/` و`uploads/`) — نسخه احتياطياً.
- الحاوية تنشر `127.0.0.1:8000` فقط؛ ضع nginx/Caddy أمامها لـ HTTPS
  (`deploy/nginx.conf` أو `deploy/Caddyfile`).
- الترقية: `docker compose down` ثم استبدال الملفات ثم `docker compose up -d --build`
  — البيانات محفوظة في `./data`.

---

## د. Cloudflare Tunnel (رابط عام بدون فتح منافذ)

مناسب لمحل خلف شبكة مزوّد بدون IP ثابت، أو لإتاحة النظام لموظفين عن بُعد.

1. ثبّت cloudflared على الجهاز الذي يعمل عليه النظام.
2. تجربة سريعة (رابط مؤقت):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. رابط ثابت:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create sanadex
   cloudflared tunnel route dns sanadex sanadex.wanis.online
   cloudflared tunnel run --url http://localhost:8000 sanadex
   ```
4. ثبّته كخدمة: `cloudflared service install` ليبدأ مع ويندوز.
5. في لوحة Cloudflare أضف سياسة **Access** (OTP بالبريد) لحماية إضافية.

---

## ٥. النسخ الاحتياطي على السيرفر

النظام يكتب `backups/sanadex-backup-YYYYMMDD-HHMMSS.db` باستخدام واجهة SQLite
الآمنة أثناء العمل، ويحتفظ بآخر ٤٠ نسخة. طبقتان إضافيتان:

```bash
0 2 * * * cp -r /opt/sanadex/data/backups /var/backups/sanadex-$(date +\%F) 2>/dev/null
```
مع Google Drive من الإعدادات (يحتاج OAuth خاص بك — انظر README).

استعادة من سطر الأوامر:
```bash
sudo systemctl stop sanadex
sudo -u sanadex /opt/sanadex/.venv/bin/python /opt/sanadex/manage.py restore \
     /opt/sanadex/data/backups/sanadex-backup-20261021-020000.db
sudo systemctl start sanadex
```
**اختبر الاستعادة قبل أن تحتاجها.**

---

## ٦. الترقية

1. خذ نسخة احتياطية يدوية.
2. أوقف الخدمة (`systemctl stop sanadex` أو `docker compose down` أو أغلق نافذة التشغيل).
3. استبدل ملفات البرنامج — **مع الاحتفاظ** بـ `sanadex.db*` و`backups/` و`uploads/` و`.env`.
4. ابدأ الخدمة. تُرقّى قاعدة البيانات تلقائياً (إضافة أعمدة فقط، دون حذف بيانات).
5. افتح الصفحة، سجّل الدخول، راجع تقرير الأرباح والخسائر وآخر فاتورة.

---

## ٧. حل المشاكل

| المشكلة | السبب والحل |
| --- | --- |
| **«اسم المستخدم أو كلمة المرور غير صحيحة»** وأنت تكتب `admin123` | غالباً المتصفح يملأ **كلمة مرور قديمة محفوظة** (البرنامج القديم كان على نفس `localhost:8000`). امسح خانة كلمة المرور واكتبها يدوياً — أو شغّل `python manage.py reset-admin` واستخدم الكلمة التي يطبعها. |
| فقدت كلمة المرور | `python manage.py reset-admin` → يطبع مسار قاعدة البيانات ويضع كلمة مرور جديدة ويعيد تفعيل الحساب. و`python manage.py info` يعرض المستخدمين وقاعدة البيانات المستخدمة. |
| `Address already in use` / المنفذ 8000 مشغول | نسخة أخرى تعمل: `netstat -ano \| findstr :8000` ثم أوقف العملية، أو `systemctl stop sanadex`. |
| الدخول يعمل لكن البيانات فارغة | أنت على قاعدة بيانات أخرى. سطر `Database:` في نافذة التشغيل يوضح المسار؛ قارنه بـ `manage.py info` وانقل `sanadex.db` الصحيح. |
| صفحة خطأ 500 | أعد التشغيل مع `SANADEX_DEBUG=1` لعرض التفاصيل للمدير، وتأكد أن `backups/` و`uploads/` قابلة للكتابة. |
| `database is locked` | نسختان تعملان، أو الملف على OneDrive/شبكة. اجعله على قرص محلي وشغّل نسخة واحدة (خيوط waitress آمنة). |
| Google Drive لا يتصل | يجب تطابق Redirect URI (`http://localhost:8000/gdrive/callback`)، وتفعيل Drive API، ولصق client secret، واتصال إنترنت. |
| أخطاء SQL Server | `pip install pyodbc` + تثبيت ODBC Driver 18 + مراجعة `SANADEX_MSSQL_DSN` + صلاحية إنشاء جداول. |
| روابط أو صور لا تظهر خلف بروكسي | مرّر `X-Forwarded-Proto` (الملفان الجاهزان يفعلان ذلك). |

فحص الصحة: `GET /healthz` يعيد JSON بدون تسجيل دخول — استخدمه في UptimeRobot
أو Docker healthcheck أو مراقبة nginx.

---

## ٨. قائمة التحقق النهائية

- [ ] ضبط `SANADEX_SECRET` وإعادة التشغيل
- [ ] تغيير كلمة مرور المدير + إنشاء حساب مدير ثانٍ
- [ ] HTTPS مفعّل مع تحويل HTTP إليه
- [ ] المنفذ 8000 غير متاح من الإنترنت
- [ ] النسخ التلقائي يعمل + Google Drive مربوط + **تجربة استعادة واحدة**
- [ ] مراقبة `/healthz`
- [ ] قاعدة البيانات على قرص محلي ومستثناة من أي مزامنة
- [ ] مستخدم لكل موظف (بدون حسابات مشتركة) ومراجعة الأدوار
- [ ] توليد ترخيص للعميل من شاشة «الترخيص» وتفعيله
