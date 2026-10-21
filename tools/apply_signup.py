# -*- coding: utf-8 -*-
"""Apply the sign-up flow, pending-user indicators and translations."""
import io
import sys

BASE = r'C:\Users\emp_s\OneDrive\Documents\deepseek-harness\default-workspace\sanadex'


def read(name):
    return io.open(BASE + '\\' + name, encoding='utf-8').read()


def write(name, text):
    io.open(BASE + '\\' + name, 'w', encoding='utf-8', newline='\n').write(text)


# ------------------------------------------------------------------ views_main
text = read('views_main.py')
if 'import re\nimport time' not in text:
    text = text.replace('import platform\nimport time', 'import platform\nimport re\nimport time')
print('re import:', 'import re\nimport time' in text)

SIGNUP = '''

@router.route('signup', '/signup', ('GET', 'POST'))
def signup(req):
    """Self registration: the account is created disabled and needs admin approval."""
    if req.user:
        return redirect(url_for('dashboard'), req)
    allowed = int(req.company.get('allow_signup') or 0) == 1
    error = ''
    form = {key: req.f(key) for key in ('full_name', 'username', 'phone', 'email')}
    if req.method == 'POST' and allowed:
        full_name = (req.f('full_name') or '').strip()
        username = (req.f('username') or '').strip().lower()
        phone = (req.f('phone') or '').strip()
        email = (req.f('email') or '').strip()
        password = req.f('password') or ''
        confirm = req.f('password2') or ''
        if not re.match(r'^[a-z0-9_.-]{3,32}$', username):
            error = T(req, 'signup_username_invalid')
        elif not full_name:
            error = T(req, 'required_field') + ': ' + T(req, 'full_name')
        elif len(password) < 6:
            error = T(req, 'signup_short_password')
        elif password != confirm:
            error = T(req, 'signup_mismatch')
        elif one(req.db, 'SELECT id FROM users WHERE username=?', (username,)):
            error = T(req, 'signup_username_taken')
        else:
            user_id = x(req.db, 'INSERT INTO users(username,password,full_name,phone,email,role_id,'
                                'is_admin,active,lang,created_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (username, hash_password(password), full_name, phone, email, 0, 0, 0, '',
                         time.time(), 'signup'))
            audit(req, 'create', 'users', user_id, f'signup pending: {username}')
            flash(req, T(req, 'signup_pending_msg'), 'success')
            return redirect(url_for('login'), req)
    return render(req, 'signup.html', title=T(req, 'signup'), allowed=allowed,
                  error=error, form=form)


'''
anchor = "@router.route('healthz', '/healthz', ('GET',))"
assert anchor in text, 'health route anchor missing'
if "route('signup'" not in text:
    text = text.replace(anchor, SIGNUP + anchor, 1)
print('signup route:', "route('signup'" in text)

if 'pending_users_alert' not in text:
    old = "    for row in q(conn, 'SELECT name, balance, credit_limit FROM customers '"
    new = ("    pending = scalar(conn, 'SELECT COUNT(*) c FROM users WHERE active=0')\n"
           "    if pending:\n"
           "        alerts.append({'icon': 'users', 'tone': 'info', 'route': 'users',\n"
           "                       'text': T(req, 'pending_users_alert', n=pending)})\n"
           + old)
    assert old in text, 'dashboard alerts anchor missing'
    text = text.replace(old, new, 1)
print('pending alert:', 'pending_users_alert' in text)

if 'allow_signup' not in text:
    old = "SETTINGS_FLAGS = ['auto_backup', 'restrict_scope', 'show_cost', 'gdrive_enabled']"
    new = ("SETTINGS_FLAGS = ['auto_backup', 'restrict_scope', 'show_cost', 'gdrive_enabled',\n"
           "                  'allow_signup']")
    assert old in text, 'settings flags anchor missing'
    text = text.replace(old, new, 1)
print('allow_signup flag:', "'allow_signup'" in text)
write('views_main.py', text)

# ------------------------------------------------------------------ settings.html
text = read('templates/settings.html')
if 'name="allow_signup"' not in text:
    old = """          <label class="check"><input type="checkbox" name="show_cost" {{ 'checked' if comp.show_cost }}>
            <span class="tiny">{{ t('show_cost') }}</span></label>"""
    new = old + """
          <label class="check"><input type="checkbox" name="allow_signup"
                 {{ 'checked' if comp.allow_signup }}>
            <span class="tiny">{{ t('settings_signup') }}</span></label>"""
    assert old in text, 'settings show_cost anchor missing'
    text = text.replace(old, new, 1)
print('settings toggle:', 'name="allow_signup"' in text)
write('templates/settings.html', text)

# ------------------------------------------------------------------ crud.py
text = read('crud.py')
if 'def f_active(' not in text:
    helper = """

def f_active(key='active'):
    def fmt(req, row):
        return T(req, 'active') if row.get(key) else T(req, 'pending_approval')
    return fmt

"""
    anchor = "def f_date(key='date'):"
    assert anchor in text, 'f_date anchor missing'
    text = text.replace(anchor, helper.strip('\n') + "\n\n\ndef f_date(key='date'):", 1)
    old = "L('is_admin', 'is_admin', fmt=f_bool('is_admin')), L('active', 'active', fmt=f_bool('active'))],"
    new = "L('is_admin', 'is_admin', fmt=f_bool('is_admin')), L('active', 'active', fmt=f_active('active'))],"
    assert old in text, 'users list columns anchor missing'
    text = text.replace(old, new, 1)
print('crud f_active:', 'def f_active(' in text)
write('crud.py', text)

# ------------------------------------------------------------------ i18n.py
text = read('i18n.py')
EXTRA = """    'signup': ('إنشاء حساب', 'Create account'),
    'signup_title': ('إنشاء حساب جديد', 'Create a new account'),
    'signup_hint': ('يُنشأ الحساب موقوفاً حتى يوافق عليه مدير النظام، ثم تبدأ تجربتك 14 يوماً.',
                    'The account is created disabled until an administrator approves it, then your 14-day trial starts.'),
    'signup_submit': ('إرسال طلب الحساب', 'Request account'),
    'signup_have_account': ('لديك حساب بالفعل؟', 'Already have an account?'),
    'signup_pending_msg': ('تم إرسال طلبك — سيتمكن المدير من تفعيل الحساب قريباً.',
                           'Request sent — an administrator will activate the account shortly.'),
    'signup_pending_title': ('طلبك بانتظار الموافقة', 'Your request is pending approval'),
    'signup_disabled': ('إنشاء الحسابات من الشاشة معطّل حالياً. اطلب من المدير إنشاء حساب لك.',
                        'Self sign-up is disabled. Ask your administrator to create an account.'),
    'signup_username_invalid': ('اسم المستخدم يجب أن يكون 3–32 حرفاً إنجليزياً أو رقماً (a-z 0-9 . _ -).',
                                'Username must be 3–32 characters: a-z, 0-9, dot, underscore or dash.'),
    'signup_username_taken': ('اسم المستخدم مستخدم بالفعل.', 'That username is already taken.'),
    'signup_mismatch': ('كلمتا المرور غير متطابقتين.', 'The passwords do not match.'),
    'signup_short_password': ('كلمة المرور قصيرة — 6 أحرف على الأقل.', 'Password too short — 6 characters minimum.'),
    'no_account': ('ليس لديك حساب؟', 'No account yet?'),
    'create_account': ('أنشئ حساباً', 'Create one'),
    'pending_approval': ('بانتظار الموافقة', 'Pending approval'),
    'pending_users_alert': ('{n} حساب بانتظار موافقتك من شاشة المستخدمين.',
                            '{n} account(s) waiting for your approval in Users.'),
    'settings_signup': ('السماح بطلب حساب من شاشة الدخول (بانتظار موافقة المدير)',
                        'Allow account requests from the login page (pending admin approval)'),
    'confirm_password': ('تأكيد كلمة المرور', 'Confirm password'),
    'login_no_account': ('ليس لديك حساب؟', 'No account yet?'),
"""
if "'signup':" not in text:
    marker = "\n    # ---------------------------------------------------------------- misc"
    assert marker in text, 'i18n misc marker missing'
    text = text.replace(marker, "\n    # ---------------------------------------------------------------- self sign-up\n" + EXTRA + marker, 1)
print('i18n signup:', "'signup':" in text)
write('i18n.py', text)
print('done')
