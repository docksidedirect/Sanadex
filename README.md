# Sanadex.wanis.online

**نظام إدارة المنشآت والمحلات — محاسبة · مخازن · مبيعات · خزائن · تقارير**
Accounting · stock · sales · cash · reports — bilingual (Arabic / English), offline-first.

Sanadex.wanis.online runs on a bare Python install: no Flask, no internet, no
database server required. It ships with SQLite and can be pointed at Microsoft
SQL Server.

---

## التشغيل / Run

1. ثبّت Python 3.10 أو أحدث.
2. `pip install -r requirements.txt` (اختياري — النظام يعمل بدونه ويصدّر Excel بمحرّك داخلي).
3. شغّل `run.bat` (ويندوز) أو `./run.sh` (لينكس/ماك) أو `python app.py`.
4. افتح <http://localhost:8000> — الدخول الافتراضي `admin` / `admin123`
   (غيّره فوراً من شاشة المستخدمين).

المنفذ قابل للتغيير: `PORT=9000 python app.py`.

---

## المميزات / Features

### الأساس
- واجهة عربية وإنجليزية كاملة (RTL / LTR) تُحفظ لكل مستخدم.
- تصميم متجاوب: قائمة جانبية قابلة للطي + زر هامبرغر على الهاتف، ووضع ليلي.
- بحث شامل (Ctrl+K) لكل شاشة وصنف وعميل وفاتورة.
- سجل نشاط (Audit log) لكل إضافة/تعديل/حذف.

### المنشأة والصلاحيات
- تسجيل بيانات المنشأة أو الشركة أو المحل وتحميل الشعار (يظهر في الفواتير).
- مستخدمون وأدوار وصلاحيات دقيقة (مبيعات، مشتريات، مخزون، خزائن، تقارير،
  أقساط، خدمات، موارد بشرية، حسابات، إعدادات).
- تخصيص خزينة ومخزن لكل مستخدم مع تقييد الشاشات عليهما.
- تعدد الخزائن والحسابات البنكية والتحويل بينها.

### الأصناف والمخازن
- عدد غير محدود من المخازن والأصناف، واستيراد من Excel/CSV وتصدير إليه.
- وحدات متعددة للصنف (كرتونة، دستة، قطعة…) وبازكود مختلف لكل وحدة.
- شرائح أسعار غير محدودة (قطاعي، جملة، نص جملة…) وشريحة مخصصة لكل عميل،
  تُطبّق تلقائياً في الفاتورة مع إمكانية تعديل السعر يدوياً.
- تواريخ الصلاحية وتنبيه الأصناف القريبة من الانتهاء، وتنبيه حد إعادة الطلب.
- توالف، تسويات مخزون، إذون تحويل بين المخازن مع كشف وطباعة.
- ملصقات باركود قابلة للطباعة (Code128 حقيقي يعمل دون إنترنت).

### الفواتير والخزائن
- فواتير بيع وشراء ضريبية وغير ضريبية، بادئة ولاحقة للأرقام، آجل/جزئي/مدفوع،
  إلغاء يعكس كل التأثيرات، نسخ فاتورة، وتحصيل دفعات.
- عروض أسعار وتخفيضات بفترة محددة.
- سندات قبض وصرف، مسحوبات شخصية وهدايا، شيكات مؤجلة الدفع مع تحصيل/ارتجاع.
- كشف حساب للعملاء والموردين، إشعارات خصم وإضافة، أعمار الديون.
- طباعة A4 / A5 / حرارة (ريسيت) وقارئ باركود في شاشة الفاتورة.

### الطلبات والتنبيهات
- طلبات بيع وشراء، تحويل الطلب إلى فاتورة بضغطة، تنبيه الطلبات المتأخرة.
- تذكيرات شخصية وتنبيهات على لوحة التحكم (نواقص، صلاحيات، أقساط، شيكات).

### التقارير
- أرباح الأصناف والفواتير، الأرباح والخسائر، تقييم المخزون، الصلاحيات،
  النواقص، الأكثر/الأقل مبيعاً، كشف الحساب، أعمار الديون، الضريبة،
  كارت الصنف، المشتريات حسب المورد، المبيعات حسب العميل، الأصناف المحولة،
  وحركة الخزائن — وكل تقرير يُصدَّر Excel.

### الحسابات العامة
- شجرة حسابات جاهزة (أصول/خصوم/حقوق ملكية/إيرادات/مصروفات) وقابلة للتعديل.
- قيود يومية متوازنة، ميزان مراجعة، ميزانية عمومية، تدفقات نقدية،
  تدفقات رأس المال.

### الأقساط والخدمات والموارد البشرية
- خطط أقساط (أيام/أسابيع/أشهر/سنوات) مع تقرير المستحق والمتأخر وتحصيل الأقساط.
- أوامر خدمات للمراكز والمغاسل مع حالات وتحصيل دفعات.
- موظفون، أدوار وظيفية، حزم رواتب، حضور وغياب (تسجيل جماعي)، عطلات،
  مكافآت وخصومات، سلف، ومسير رواتب شهري/يومي مع قسيمة قابلة للطباعة.

### النسخ الاحتياطي
- نسخة احتياطية تلقائية كل مدة محددة (افتراضياً 24 ساعة) ونسخ يدوية.
- رفع تلقائي إلى **Google Drive** بحسابك (OAuth) واستعادة أي نسخة من Drive.
- استعادة من ملف نسخة محمّل، مع تحقق من صحة الملف.

---

## قاعدة البيانات / Database

- **افتراضياً:** SQLite مدمجة (ملف `sanadex.db`) — صفر تثبيت، ونسخ احتياطي بسيط.
- تُرقّى قاعدة بيانات `sahm.db` القديمة تلقائياً عند أول تشغيل (يُحتفظ بالبيانات).
- **SQL Server:** عيّن متغيّرات البيئة قبل التشغيل:

```bat
set SANADEX_DB=mssql
set SANADEX_MSSQL_DSN=DRIVER={ODBC Driver 18 for SQL Server};SERVER=.;DATABASE=Sanadex;UID=sa;PWD=...
python app.py
```

المخطط يُنشأ تلقائياً على المحرّكين، والأعمدة الجديدة تُضاف دون حذف بيانات.
يتطلب محرّك SQL Server تثبيت `pyodbc` وبرنامج تشغيل ODBC.

متغيّرات أخرى: `SANADEX_DB_PATH`، `SANADEX_BACKUP_DIR`، `SANADEX_UPLOAD_DIR`،
`SANADEX_SECRET` (مفتاح الجلسات)، `SANADEX_DEBUG=1`.

---

## Google Drive

1. أنشئ OAuth Client (نوع Web) من Google Cloud Console وفعّل Drive API.
2. أضف `http://localhost:8000/gdrive/callback` كـ Redirect URI.
3. من **النسخ الاحتياطي** الصق Client ID و Secret ثم اضغط «ربط».
4. بعد الربط تُرفع كل نسخة تلقائياً، ويمكن الاستعادة مباشرة من Drive.

---

## الترخيص / Licence

**نموذج الترخيص:**
- **حساب المدير (admin): ترخيص دائم** — لا ينتهي، ولا يظهر له أي شريط تجربة.
- **أي مستخدم آخر: تجربة 14 يوماً** تبدأ من أول تسجيل دخول له، مع شريط يوضح الأيام
  المتبقية. عند الانتهاء تُعرض له شاشة «انتهت الفترة التجريبية» (البيانات سليمة)
  حتى يفعّل المدير ترخيصاً.
- المدير من شاشة **الترخيص** يستطيع: تجديد تجربة مستخدم، منحه ترخيصاً دائماً،
  إلغاء ذلك، أو تفعيل **ترخيص للمنشأة** يغطي كل المستخدمين.

**المولّد والتفعيل:**


- شاشة **الترخيص** للمدير فقط: توليد مفتاح موقّع (HMAC-SHA256) دون إنترنت، وتفعيله،
  وسجل التراخيص، وإلغاء التفعيل، وتحميل ملف الترخيص، ونسخة تجريبية ١٤ يوماً.
- المفتاح يحمل اسم العميل والإصدار وعدد المستخدمين وتاريخ الانتهاء، ويمكن ربطه
  بمعرّف الجهاز (`license.machine_id()`) أو تركه لأي جهاز.
- ضع `SANADEX_LICENSE_SECRET` بقيمة خاصة بك على جهاز الإصدار فقط؛ بها تُوقّع
  المفاتيح ويُتحقق منها. المفاتيح المولّدة بأي سر آخر تُرفض.
- عند غياب ترخيص ساري يظهر شريط تنبيه أعلى الشاشة (تجريبي/منتهي) دون تعطيل العمل.

## النشر / Deployment

دليل كامل بالعربية في **`DEPLOY_AR.md`** وبالإنجليزية في **`DEPLOY.md`**، ويغطي:
تشغيل ويندوز والشبكة المحلية، سيرفر VPS مع دومين وHTTPS (nginx + systemd)،
Docker، وCloudflare Tunnel بدون فتح منافذ — مع قوائم تحقق للأسرار والنسخ
الاحتياطي والترقية.

ملفات جاهزة: `Dockerfile`، `docker-compose.yml`، `.env.example`،
`deploy/nginx.conf`، `deploy/Caddyfile`، `deploy/sanadex.service`،
`run_prod.py` (waitress)، و`run-prod.bat` لويندوز.

```bash
cp .env.example .env      # ضع SANADEX_SECRET
docker compose up -d --build
curl -fsS http://127.0.0.1:8000/healthz
```

### استكشاف الأعطال وحدة التحكم

```bash
python manage.py info              # مسار قاعدة البيانات + المستخدمون + عدد السجلات
python manage.py reset-admin       # إعادة تعيين كلمة مرور المدير (يطبعها)
python manage.py check             # فحص سلامة قاعدة البيانات
python manage.py backup            # نسخة احتياطية فورية
python manage.py restore <file>    # استعادة نسخة
python manage.py license           # حالة الترخيص / توليد وتفعيل مفتاح
```

على ويندوز يمكن النقر المزدوج على **`reset-admin.bat`** بدلاً من كتابة الأوامر.

**لا تستطيع الدخول؟** في الغالب المتصفح يملأ كلمة مرور قديمة محفوظة لنفس العنوان.
امسح خانة كلمة المرور واكتبها يدوياً، أو شغّل `reset-admin.bat` واستخدم الكلمة
التي يطبعها. سطر `Database:` في نافذة التشغيل يوضح قاعدة البيانات المستخدمة
فعلاً — قارنه بـ `manage.py info` إن ظهرت البيانات فارغة.

فحص الصحة: `GET /healthz` (بدون تسجيل دخول) يعيد JSON للمراقبة.

## البنية / Project layout

```
app.py            WSGI entry point + dispatch
framework.py      micro WSGI framework (stdlib only)
core.py           rendering, navigation, permissions, stock & cash primitives
crud.py           declarative CRUD engine + master-data screens
views_main.py     auth, dashboard, settings, backups, Drive, search, audit, demo
views_sales.py    invoices, payments, orders, conversion
views_stock.py    items, units, transfers, barcodes, Excel import/export
views_money.py    vouchers, cheques, installments, service payments
views_reports.py  reports + accounting statements
views_hr.py       payroll and attendance
db.py             schema, migrations, backups (SQLite / SQL Server)
i18n.py           Arabic + English dictionary (714 keys)
xlsx.py           dependency-free .xlsx writer/reader
barcode.py        Code128 encoder + SVG renderer
gdrive.py         Google Drive OAuth + upload/download
templates/        Jinja2 screens (bilingual, print-ready)
static/           app.css design system + app.js behaviour
tests/            end-to-end acceptance suite
tools/            developer helpers (smoke test, i18n check)
```

---

## الاختبارات / Tests

```
python tests/test_e2e.py
```

The suite logs in, exercises every module (invoices, cancel, payments, tiers,
offers, transfers, damages, vouchers, cheques, orders, installments, payroll,
accounting, reports, Excel import/export, barcodes, permissions, backup and
restore) and asserts stock, cash and party balances after each step.

`python tools/smoke.py` renders every GET route and fails on any non-200.
`python tools/check_i18n.py` reports keys used in code/templates but missing
from the dictionary.

---

## ملاحظات
- النظام يعمل دون إنترنت بالكامل عدا مزامنة Google Drive.
- الصور والملفات المرفوعة تُحفظ في `uploads/`، والنسخ في `backups/`.
- غيّر كلمة مرور `admin` من أول تشغيل، وأنشئ مستخدماً لكل موظف بدل المشاركة.
