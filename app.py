"""
TranscribeHub - Flask Application
- Admin-only task posting and review.
- Public visitors can browse the site and preview tasks freely.
- Workers/transcribers must Sign Up / Log In and pay an entry fee of KES 2,500 via Paystack
  (supports M-Pesa, Card, Bank) to activate their account and start working.
"""
import os, json, re, uuid
from datetime import datetime, timedelta
import requests
from werkzeug.utils import secure_filename
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, session, send_from_directory, Response, abort
)
import database
import exporter

app = Flask(__name__)
app.secret_key = 'transcribehub-prod-secret-2024-change-me'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024   # 500 MB
ALLOWED_EXTENSIONS = {'mp4', 'webm', 'mov', 'm4v', 'avi', 'mkv', 'mp3', 'wav', 'ogg', 'm4a', 'flac'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
database.init_db()


# ─── Auth & User Helpers ──────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_admin():
    return session.get('is_admin', False)


def get_current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    return database.get_user_by_id(user_id)


def require_admin(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_admin():
            flash('Please log in as admin to access this page.', 'warning')
            return redirect(url_for('admin_login', next=request.path))
        return func(*args, **kwargs)
    return wrapper


def require_user(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            flash('Please log in or create an account to continue.', 'info')
            return redirect(url_for('login', next=request.path))
        return func(*args, **kwargs)
    return wrapper


def require_paid_worker(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            flash('Please log in or sign up to claim tasks.', 'info')
            return redirect(url_for('login', next=request.path))
        if not user.get('is_paid'):
            amount, curr = database.get_entry_fee()
            flash(f'An entry fee of {curr} {amount:,} via Paystack is required to activate your working account.', 'warning')
            return redirect(url_for('pay_entry_fee', next=request.path))
        return func(*args, **kwargs)
    return wrapper


def format_youtube(url):
    m = re.search(
        r'(?:youtube\.com/(?:[^/]+/.+/|(?:v|e(?:mbed)?)/|.*[?&]v=)|youtu\.be/)([^"&?/\s]{11})',
        url
    )
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}?enablejsapi=1"
    return url


def upload_to_r2(local_filepath: str, filename: str) -> tuple[bool, str]:
    """
    Uploads a file to Cloudflare R2 bucket using S3-compatible credentials.
    Returns (True, public_url) on success, or (False, error_message) on failure.
    """
    endpoint = database.get_setting('r2_endpoint_url', '').strip()
    key_id   = database.get_setting('r2_access_key_id', '').strip()
    secret   = database.get_setting('r2_secret_access_key', '').strip()
    bucket   = database.get_setting('r2_bucket_name', 'transcribe-tasks').strip()
    pub_url  = database.get_setting('r2_public_url', '').strip().rstrip('/')

    if not (endpoint and key_id and secret and pub_url):
        return False, "R2 credentials not fully configured."

    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            's3',
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            config=Config(signature_version='s3v4')
        )

        content_type = 'video/mp4'
        if filename.endswith('.webm'):
            content_type = 'video/webm'
        elif filename.endswith(('.mp3', '.wav', '.ogg')):
            content_type = 'audio/mpeg'

        s3.upload_file(
            local_filepath,
            bucket,
            filename,
            ExtraArgs={'ContentType': content_type}
        )
        return True, f"{pub_url}/{filename}"
    except ImportError:
        # If running in environment without boto3 yet
        return False, "boto3 package not installed on local environment"
    except Exception as e:
        return False, str(e)


@app.context_processor
def inject_globals():
    conn = database.get_db_connection()

    open_count     = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0]
    bounty_pool    = conn.execute("SELECT SUM(reward) FROM tasks WHERE status='open'").fetchone()[0] or 0.0
    completed_count= conn.execute("SELECT COUNT(*) FROM tasks WHERE status='completed'").fetchone()[0]
    pending_reviews= conn.execute("SELECT COUNT(*) FROM tasks WHERE status='submitted'").fetchone()[0]

    conn.close()

    current_user = get_current_user()
    fee_amount, fee_currency = database.get_entry_fee()
    paystack_pub = database.get_setting('paystack_public_key', '')

    return dict(
        is_admin=is_admin(),
        current_user=current_user,
        entry_fee=fee_amount,
        entry_currency=fee_currency,
        paystack_public_key=paystack_pub,
        open_count=open_count,
        bounty_pool=bounty_pool,
        completed_count=completed_count,
        pending_reviews=pending_reviews,
        now=datetime.now()
    )


# ─── Worker Auth (Signup, Login, Logout) ──────────────────────────────────────

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        full_name      = request.form.get('full_name', '').strip()
        username       = request.form.get('username', '').strip()
        email          = request.form.get('email', '').strip()
        phone          = request.form.get('phone', '').strip()
        payout_method  = request.form.get('payout_method', '').strip()
        payout_account = request.form.get('payout_account', '').strip()
        payout_details = f"{payout_method}: {payout_account}".strip(': ') if payout_method or payout_account else ''
        password  = request.form.get('password', '')
        confirm_p = request.form.get('confirm_password', '')

        if not full_name or not username or not email or not password:
            flash('All required fields must be filled.', 'error')
            return render_template('signup.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('signup.html')

        if password != confirm_p:
            flash('Passwords do not match.', 'error')
            return render_template('signup.html')

        success, msg = database.create_user(username, email, password, full_name, phone, payout_details)
        if success:
            user = database.authenticate_user(username, password)
            session['user_id'] = user['id']
            session.permanent = True
            flash(f'Welcome, {full_name}! Account created. Please activate with Paystack to start working.', 'success')
            next_url = request.args.get('next') or url_for('pay_entry_fee')
            return redirect(next_url)
        else:
            flash(msg, 'error')

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        login_id = request.form.get('login_id', '').strip()
        password = request.form.get('password', '')

        user = database.authenticate_user(login_id, password)
        if user:
            session['user_id'] = user['id']
            session.permanent = True
            flash(f'Welcome back, {user["full_name"]}!', 'success')
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            flash('Invalid username/email or password.', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))


# ─── Forgot / Reset Password ──────────────────────────────────────────────────

def _send_reset_email(to_email: str, reset_link: str, username: str) -> tuple[bool, str]:
    """Send a password reset email using SMTP settings from the database."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    smtp_host = database.get_setting('smtp_host', '').strip()
    smtp_port = int(database.get_setting('smtp_port', '587') or '587')
    smtp_user = database.get_setting('smtp_user', '').strip()
    smtp_pass = database.get_setting('smtp_password', '').strip()
    smtp_from = database.get_setting('smtp_from_email', smtp_user).strip()

    if not all([smtp_host, smtp_user, smtp_pass]):
        return False, "Email is not configured. Ask the admin to set SMTP settings."

    subject = "TranscribeHub — Password Reset Request"
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;background:#0f172a;color:#e2e8f0;border-radius:16px;padding:32px;">
      <h2 style="color:#818cf8;margin-bottom:8px;">Reset Your Password</h2>
      <p style="color:#94a3b8;font-size:14px;">Hi <strong style="color:#e2e8f0;">{username}</strong>,</p>
      <p style="color:#94a3b8;font-size:14px;">We received a request to reset your TranscribeHub password.
         Click the button below to set a new one. This link expires in <strong>1 hour</strong>.</p>
      <div style="text-align:center;margin:28px 0;">
        <a href="{reset_link}" style="background:#4f46e5;color:#fff;text-decoration:none;padding:14px 32px;border-radius:10px;font-weight:bold;font-size:14px;display:inline-block;">
          Reset My Password →
        </a>
      </div>
      <p style="color:#64748b;font-size:12px;">If you didn't request this, you can safely ignore this email.<br>
         Your password will <strong>not</strong> change unless you click the link above.</p>
      <hr style="border-color:#1e293b;margin:20px 0;">
      <p style="color:#334155;font-size:11px;text-align:center;">TranscribeHub &mdash; Secure Transcription Platform</p>
    </div>
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = smtp_from
    msg['To']      = to_email
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [to_email], msg.as_string())
        server.quit()
        return True, "Email sent."
    except Exception as e:
        return False, str(e)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        if not email:
            flash('Please enter your email address.', 'error')
            return render_template('forgot_password.html')

        ok, token = database.create_reset_token(email)

        # Always show the same message to prevent email enumeration
        if ok and token != '__no_user__':
            reset_link = url_for('reset_password', token=token, _external=True)
            # Try to fetch username for the email
            conn = database.get_db_connection()
            user_row = conn.execute('SELECT username FROM users WHERE email = ?', (email,)).fetchone()
            conn.close()
            uname = user_row['username'] if user_row else 'Transcriber'
            sent, err = _send_reset_email(email, reset_link, uname)
            if not sent:
                # If SMTP is not configured, show the link in flash for dev/testing convenience
                flash(
                    f'Email delivery failed ({err}). '
                    f'Reset link (dev only): <a href="{reset_link}" class="underline text-indigo-400">{reset_link}</a>',
                    'warning'
                )
                return render_template('forgot_password.html')

        flash('If that email is registered, a password reset link has been sent. Check your inbox (and spam folder).', 'success')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user, reset_id = database.verify_reset_token(token)
    if not user:
        flash('This password reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_pass    = request.form.get('password', '')
        confirm     = request.form.get('confirm_password', '')

        if len(new_pass) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html', token=token, username=user['username'])
        if new_pass != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token, username=user['username'])

        database.consume_reset_token(reset_id, user['id'], new_pass)
        flash('Your password has been reset successfully. Please log in with your new password.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token, username=user['username'])



@app.route('/pay-entry-fee')
@require_user
def pay_entry_fee():
    user = get_current_user()
    if user.get('is_paid'):
        flash('Your account is already activated and paid!', 'info')
        return redirect(url_for('index'))

    amount, currency = database.get_entry_fee()
    pub_key = database.get_setting('paystack_public_key', '')

    return render_template('pay_entry_fee.html',
                           user=user,
                           amount=amount,
                           currency=currency,
                           paystack_public_key=pub_key)


@app.route('/api/paystack/initialize', methods=['POST'])
@require_user
def paystack_initialize():
    """Initializes transaction with Paystack API and returns authorization_url."""
    user = get_current_user()
    secret_key = database.get_setting('paystack_secret_key', '').strip()
    amount, currency = database.get_entry_fee()

    # Paystack amount in lowest currency unit (cents/subunits: KES 2500 -> 250000)
    amount_in_subunits = int(amount * 100)
    tx_ref = f"TH-{user['id']}-{uuid.uuid4().hex[:8].upper()}"

    callback_url = request.host_url.rstrip('/') + url_for('paystack_callback')

    # If test mode or live mode with valid secret key
    if secret_key and not secret_key.startswith('sk_test_sample'):
        headers = {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "email": user['email'],
            "amount": amount_in_subunits,
            "currency": currency,
            "reference": tx_ref,
            "callback_url": callback_url,
            "metadata": {
                "user_id": user['id'],
                "username": user['username'],
                "full_name": user['full_name'],
                "purpose": "entry_fee"
            }
        }
        try:
            resp = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers=headers, timeout=12)
            data = resp.json()
            if data.get("status") and data.get("data"):
                return jsonify({
                    "status": "success",
                    "authorization_url": data["data"]["authorization_url"],
                    "reference": tx_ref
                })
            else:
                return jsonify({"status": "error", "message": data.get("message", "Paystack initialization failed")}), 400
        except Exception as e:
            # When sandbox / offline or mock keys are tested, gracefully fallback to simulation mode
            return jsonify({
                "status": "simulation",
                "message": f"Network mode ({str(e)}). Switched to simulation mode.",
                "reference": tx_ref,
                "amount": amount,
                "currency": currency
            })
    else:
        # Development / Sandbox mock mode when live keys are not yet configured in admin
        return jsonify({
            "status": "simulation",
            "message": "Paystack keys not set. Running in seamless verification mode.",
            "reference": tx_ref,
            "amount": amount,
            "currency": currency
        })


@app.route('/paystack/callback')
@require_user
def paystack_callback():
    """Paystack redirects the user here after completing payment."""
    reference = request.args.get('reference') or request.args.get('trxref')
    if not reference:
        flash('No payment reference found.', 'error')
        return redirect(url_for('pay_entry_fee'))

    user = get_current_user()
    secret_key = database.get_setting('paystack_secret_key', '').strip()
    amount, currency = database.get_entry_fee()

    # Verify with Paystack if real key is configured
    is_verified = False
    if secret_key and not secret_key.startswith('sk_test_sample'):
        try:
            headers = {"Authorization": f"Bearer {secret_key}"}
            resp = requests.get(f"https://api.paystack.co/transaction/verify/{reference}", headers=headers, timeout=12)
            res_data = resp.json()
            if res_data.get("status") and res_data.get("data") and res_data["data"].get("status") == "success":
                is_verified = True
                amount = float(res_data["data"]["amount"]) / 100
                currency = res_data["data"]["currency"]
            else:
                flash("Paystack payment could not be verified. Please contact support.", "error")
                return redirect(url_for('pay_entry_fee'))
        except Exception as e:
            flash(f"Error communicating with Paystack: {str(e)}", "error")
            return redirect(url_for('pay_entry_fee'))
    else:
        # Demo / Sandbox instant verification
        is_verified = True

    if is_verified:
        database.record_payment(
            user_id=user['id'],
            amount=amount,
            currency=currency,
            transaction_ref=reference,
            payment_method='paystack',
            phone=user.get('phone', ''),
            paystack_status='success'
        )
        flash(f'🎉 Payment of {currency} {amount:,.0f} via Paystack confirmed! Your account is now active.', 'success')
        return redirect(url_for('index'))

    return redirect(url_for('pay_entry_fee'))


@app.route('/api/paystack/verify', methods=['POST'])
@require_user
def paystack_verify_api():
    """Endpoint for verifying Paystack Popup / Inline JS payments."""
    data = request.get_json() or {}
    reference = data.get('reference', '').strip()
    if not reference:
        return jsonify({"status": "error", "message": "Reference required"}), 400

    user = get_current_user()
    secret_key = database.get_setting('paystack_secret_key', '').strip()
    amount, currency = database.get_entry_fee()

    is_verified = False
    if secret_key and not secret_key.startswith('sk_test_sample'):
        try:
            headers = {"Authorization": f"Bearer {secret_key}"}
            resp = requests.get(f"https://api.paystack.co/transaction/verify/{reference}", headers=headers, timeout=12)
            res_data = resp.json()
            if res_data.get("status") and res_data.get("data") and res_data["data"].get("status") == "success":
                is_verified = True
                amount = float(res_data["data"]["amount"]) / 100
                currency = res_data["data"]["currency"]
            else:
                return jsonify({"status": "error", "message": "Payment verification failed"}), 400
        except Exception as e:
            # When sandbox / offline or mock keys are tested, allow mock reference verification
            if "TH-" in reference:
                is_verified = True
            else:
                return jsonify({"status": "error", "message": str(e)}), 500
    else:
        # Simulation / Testing verification
        is_verified = True

    if is_verified:
        database.record_payment(
            user_id=user['id'],
            amount=amount,
            currency=currency,
            transaction_ref=reference,
            payment_method='paystack',
            phone=user.get('phone', ''),
            paystack_status='success'
        )
        return jsonify({"status": "success", "message": "Payment verified", "redirect_url": url_for('index')})

    return jsonify({"status": "error", "message": "Verification failed"}), 400



# ─── Worker Wallet ────────────────────────────────────────────────────────────

@app.route('/wallet')
@require_user
def wallet():
    user = get_current_user()
    conn = database.get_db_connection()

    # Full refreshed user data (balance may have been updated)
    fresh_user = conn.execute('SELECT * FROM users WHERE id = ?', (user['id'],)).fetchone()

    # Approved earnings history: tasks this worker transcribed that were approved
    earnings_history = conn.execute('''
        SELECT t.title, t.reward, t.category, tr.reviewed_at, tr.status
        FROM transcriptions tr
        JOIN tasks t ON t.id = tr.task_id
        WHERE tr.transcriber_id = ? AND tr.status = 'approved'
        ORDER BY tr.reviewed_at DESC
    ''', (user['id'],)).fetchall()

    # Pending: submitted but not yet reviewed
    pending_tasks = conn.execute('''
        SELECT t.title, t.reward, t.category, tr.submitted_at
        FROM transcriptions tr
        JOIN tasks t ON t.id = tr.task_id
        WHERE tr.transcriber_id = ? AND tr.status = 'submitted'
        ORDER BY tr.submitted_at DESC
    ''', (user['id'],)).fetchall()

    # In-progress: claimed / draft
    active_tasks = conn.execute('''
        SELECT t.title, t.reward, t.category, t.claimed_at
        FROM tasks t
        WHERE t.claimed_by = ? AND t.status = 'claimed'
        ORDER BY t.claimed_at DESC
    ''', (user['id'],)).fetchall()

    total_earned = sum(r['reward'] for r in earnings_history)
    total_pending = sum(r['reward'] for r in pending_tasks)

    conn.close()
    return render_template('wallet.html',
                           worker=dict(fresh_user),
                           earnings_history=earnings_history,
                           pending_tasks=pending_tasks,
                           active_tasks=active_tasks,
                           total_earned=total_earned,
                           total_pending=total_pending)


@app.route('/wallet/update-payout', methods=['POST'])
@require_user
def update_payout():
    user = get_current_user()
    method = request.form.get('payout_method', '').strip()
    account = request.form.get('payout_account', '').strip()

    if not method or not account:
        flash('Please select a payment method and provide your payout account.', 'error')
        return redirect(url_for('wallet'))

    payout_details = f"{method}: {account}"
    conn = database.get_db_connection()
    conn.execute('UPDATE users SET payout_details = ? WHERE id = ?', (payout_details, user['id']))
    conn.commit()
    conn.close()

    flash(f'Payout destination updated to {payout_details}!', 'success')
    return redirect(url_for('wallet'))


@app.route('/wallet/withdraw', methods=['POST'])
@require_user
def withdraw():
    user = get_current_user()
    conn = database.get_db_connection()
    fresh_user = conn.execute('SELECT * FROM users WHERE id = ?', (user['id'],)).fetchone()
    balance = fresh_user['balance'] if fresh_user else 0.0
    payout_details = fresh_user['payout_details'] if fresh_user else ''

    if not payout_details:
        conn.close()
        flash('Please set your payout destination before requesting a withdrawal.', 'warning')
        return redirect(url_for('wallet'))

    if balance <= 0:
        conn.close()
        flash('Your available balance is $0.00. You need approved earnings to request a payout.', 'error')
        return redirect(url_for('wallet'))

    conn.close()
    flash(f'Withdrawal request for ${balance:.2f} to {payout_details} received! Payouts are processed by admin within 24 hours.', 'success')
    return redirect(url_for('wallet'))


# ─── Admin Auth & Dashboard ───────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if is_admin():
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if database.verify_admin(username, password):
            session['is_admin'] = True
            session.permanent = True
            flash('Logged in as administrator.', 'success')
            return redirect(request.args.get('next') or url_for('admin_dashboard'))
        flash('Invalid admin credentials.', 'error')

    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('Admin logged out successfully.', 'info')
    return redirect(url_for('index'))


@app.route('/admin/settings', methods=['POST'])
@require_admin
def update_settings():
    """Update Paystack API keys, Entry Fee, R2 Storage, and SMTP configuration."""
    pub_key   = request.form.get('paystack_public_key', '').strip()
    sec_key   = request.form.get('paystack_secret_key', '').strip()
    fee_amt   = request.form.get('entry_fee_amount', '2500').strip()
    fee_curr  = request.form.get('entry_fee_currency', 'KES').strip().upper()

    r2_pub    = request.form.get('r2_public_url', '').strip()
    r2_ep     = request.form.get('r2_endpoint_url', '').strip()
    r2_bucket = request.form.get('r2_bucket_name', '').strip()
    r2_key    = request.form.get('r2_access_key_id', '').strip()
    r2_sec    = request.form.get('r2_secret_access_key', '').strip()

    smtp_host = request.form.get('smtp_host', '').strip()
    smtp_port = request.form.get('smtp_port', '').strip()
    smtp_user = request.form.get('smtp_user', '').strip()
    smtp_pass = request.form.get('smtp_password', '').strip()

    if pub_key:
        database.set_setting('paystack_public_key', pub_key)
    if sec_key:
        database.set_setting('paystack_secret_key', sec_key)
    if fee_amt:
        database.set_setting('entry_fee_amount', fee_amt)
    if fee_curr:
        database.set_setting('entry_fee_currency', fee_curr)

    if r2_pub:
        database.set_setting('r2_public_url', r2_pub)
    if r2_ep:
        database.set_setting('r2_endpoint_url', r2_ep)
    if r2_bucket:
        database.set_setting('r2_bucket_name', r2_bucket)
    if r2_key:
        database.set_setting('r2_access_key_id', r2_key)
    if r2_sec:
        database.set_setting('r2_secret_access_key', r2_sec)

    if smtp_host:
        database.set_setting('smtp_host', smtp_host)
    if smtp_port:
        database.set_setting('smtp_port', smtp_port)
    if smtp_user:
        database.set_setting('smtp_user', smtp_user)
    if smtp_pass:
        database.set_setting('smtp_password', smtp_pass)

    flash('Settings updated successfully!', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/change-password', methods=['POST'])
@require_admin
def change_password():
    new_pw  = request.form.get('new_password', '').strip()
    confirm = request.form.get('confirm_password', '').strip()
    if len(new_pw) < 4:
        flash('Password must be at least 4 characters.', 'error')
    elif new_pw != confirm:
        flash('Passwords do not match.', 'error')
    else:
        database.change_admin_password(new_pw)
        flash('Password updated successfully.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/users/<int:user_id>/toggle-paid', methods=['POST'])
@require_admin
def toggle_user_paid(user_id):
    conn = database.get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        conn.close()
        flash('Worker not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    new_status = 0 if user['is_paid'] else 1
    payment_ref = f"BYPASS-ADMIN-{uuid.uuid4().hex[:6].upper()}" if new_status else None
    paid_at = datetime.now() if new_status else None

    conn.execute('''
        UPDATE users
        SET is_paid = ?, paid_at = ?, payment_ref = ?
        WHERE id = ?
    ''', (new_status, paid_at, payment_ref, user_id))
    conn.commit()
    conn.close()

    status_label = "activated (bypassed entry fee)" if new_status else "deactivated"
    flash(f"Worker account '{user['full_name']}' has been {status_label}!", 'success')
    return redirect(url_for('admin_dashboard'))



@app.route('/admin')
@require_admin
def admin_dashboard():
    conn = database.get_db_connection()
    tasks = conn.execute('''
        SELECT t.*, tr.status AS transcription_status
        FROM tasks t
        LEFT JOIN transcriptions tr ON tr.task_id = t.id
        ORDER BY t.created_at DESC
    ''').fetchall()

    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    paid_users  = conn.execute("SELECT COUNT(*) FROM users WHERE is_paid = 1").fetchone()[0]
    entry_fees_collected = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'completed'").fetchone()[0]

    stats = {
        'total':     len(tasks),
        'open':      sum(1 for t in tasks if t['status'] == 'open'),
        'claimed':   sum(1 for t in tasks if t['status'] == 'claimed'),
        'submitted': sum(1 for t in tasks if t['status'] == 'submitted'),
        'completed': sum(1 for t in tasks if t['status'] == 'completed'),
        'bounty_paid': conn.execute(
            "SELECT COALESCE(SUM(reward),0) FROM tasks WHERE status='completed'"
        ).fetchone()[0],
        'total_users': total_users,
        'paid_users': paid_users,
        'entry_fees_collected': entry_fees_collected
    }

    recent_users = conn.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10").fetchall()
    recent_payments = conn.execute('''
        SELECT p.*, u.full_name, u.email
        FROM payments p
        JOIN users u ON p.user_id = u.id
        ORDER BY p.created_at DESC LIMIT 10
    ''').fetchall()

    conn.close()

    paystack_pub = database.get_setting('paystack_public_key', '')
    paystack_sec = database.get_setting('paystack_secret_key', '')
    fee_amt, fee_curr = database.get_entry_fee()

    r2_pub    = database.get_setting('r2_public_url', '')
    r2_ep     = database.get_setting('r2_endpoint_url', '')
    r2_bucket = database.get_setting('r2_bucket_name', 'transcribe-tasks')
    r2_key    = database.get_setting('r2_access_key_id', '')
    r2_sec    = database.get_setting('r2_secret_access_key', '')

    smtp_host = database.get_setting('smtp_host', 'smtp.gmail.com')
    smtp_port = database.get_setting('smtp_port', '587')
    smtp_user = database.get_setting('smtp_user', '')
    smtp_pass = database.get_setting('smtp_password', '')

    return render_template('admin_dashboard.html',
                           tasks=tasks,
                           stats=stats,
                           recent_users=recent_users,
                           recent_payments=recent_payments,
                           paystack_pub=paystack_pub,
                           paystack_sec=paystack_sec,
                           fee_amt=fee_amt,
                           fee_curr=fee_curr,
                           r2_pub=r2_pub,
                           r2_ep=r2_ep,
                           r2_bucket=r2_bucket,
                           r2_key=r2_key,
                           r2_sec=r2_sec,
                           smtp_host=smtp_host,
                           smtp_port=smtp_port,
                           smtp_user=smtp_user,
                           smtp_pass=smtp_pass)



# ─── Public Marketplace (Anyone Can View) ─────────────────────────────────────

@app.route('/')
def index():
    category   = request.args.get('category', '').strip()
    status_f   = request.args.get('status', '').strip()
    difficulty = request.args.get('difficulty', '').strip()
    search     = request.args.get('q', '').strip()
    sort_by    = request.args.get('sort', 'newest')

    conn   = database.get_db_connection()
    cursor = conn.cursor()

    q      = "SELECT * FROM tasks WHERE 1=1"
    params = []

    if category and category != 'All':
        q += ' AND category = ?';  params.append(category)
    if status_f and status_f != 'all':
        q += ' AND status = ?';    params.append(status_f)
    if difficulty:
        q += ' AND difficulty = ?'; params.append(difficulty)
    if search:
        q += ' AND (title LIKE ? OR description LIKE ?)'; params += [f'%{search}%', f'%{search}%']

    if sort_by == 'reward_desc':
        q += ' ORDER BY reward DESC'
    elif sort_by == 'reward_asc':
        q += ' ORDER BY reward ASC'
    elif sort_by == 'duration':
        q += ' ORDER BY duration_est ASC'
    else:
        q += ' ORDER BY created_at DESC'

    tasks = cursor.execute(q, params).fetchall()

    categories = [r[0] for r in cursor.execute(
        'SELECT DISTINCT category FROM tasks WHERE category IS NOT NULL'
    ).fetchall()]

    conn.close()
    return render_template(
        'index.html',
        tasks=tasks,
        categories=categories,
        selected_category=category,
        selected_status=status_f,
        selected_difficulty=difficulty,
        search_query=search,
        sort_by=sort_by,
    )


# ─── Post / Edit / Delete Task (Admin Only) ───────────────────────────────────

@app.route('/tasks/new', methods=['GET', 'POST'])
@require_admin
def post_task():
    if request.method == 'POST':
        title        = request.form.get('title', '').strip()
        description  = request.form.get('description', '').strip()
        category     = request.form.get('category', 'General').strip()
        language     = request.form.get('language', 'English (US)').strip()
        difficulty   = request.form.get('difficulty', 'Medium').strip()
        reward       = float(request.form.get('reward', 20.0) or 20.0)
        duration_est = request.form.get('duration_est', '05:00').strip()
        deadline     = request.form.get('deadline', '').strip()
        guidelines   = request.form.get('guidelines', '').strip()
        video_choice = request.form.get('video_choice', 'upload')

        if not title:
            flash('Task title is required.', 'error')
            return redirect(url_for('post_task'))

        video_type, video_source, video_filename = 'url', '', None

        if video_choice == 'upload':
            file = request.files.get('video_file')
            if file and file.filename and allowed_file(file.filename):
                safe_name    = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
                filepath     = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
                file.save(filepath)

                # Attempt Cloudflare R2 upload
                r2_ok, r2_result = upload_to_r2(filepath, safe_name)
                if r2_ok:
                    video_type     = 'url'
                    video_source   = r2_result
                    video_filename = safe_name
                else:
                    video_type     = 'upload'
                    video_source   = f'/uploads/{safe_name}'
                    video_filename = safe_name
            else:
                flash('Please upload a supported video/audio file (MP4, WebM, MOV, MP3, WAV …)', 'error')
                return redirect(url_for('post_task'))
        else:
            raw_url = request.form.get('video_url', '').strip()
            if not raw_url:
                flash('Please provide a valid video URL.', 'error')
                return redirect(url_for('post_task'))
            video_type   = 'url'
            video_source = format_youtube(raw_url)

        conn   = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tasks
              (title, description, category, language, difficulty, reward,
               duration_est, deadline, guidelines, video_type, video_source,
               video_filename, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')
        ''', (title, description, category, language, difficulty, reward,
              duration_est, deadline, guidelines, video_type, video_source, video_filename))
        task_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
            (task_id, 'Admin', 'created', f'Task published — earnings ${reward:.2f}')
        )
        conn.commit()
        conn.close()

        flash(f'Task "{title}" published successfully!', 'success')
        return redirect(url_for('task_detail', task_id=task_id))

    return render_template('post_task.html')


@app.route('/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@require_admin
def edit_task(task_id):
    conn  = database.get_db_connection()
    task  = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        flash('Task not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        title        = request.form.get('title', '').strip()
        description  = request.form.get('description', '').strip()
        category     = request.form.get('category', task['category']).strip()
        language     = request.form.get('language', task['language']).strip()
        difficulty   = request.form.get('difficulty', task['difficulty']).strip()
        reward       = float(request.form.get('reward', task['reward']) or task['reward'])
        duration_est = request.form.get('duration_est', task['duration_est']).strip()
        deadline     = request.form.get('deadline', task['deadline'] or '').strip()
        guidelines   = request.form.get('guidelines', task['guidelines'] or '').strip()

        conn.execute('''
            UPDATE tasks SET title=?, description=?, category=?, language=?, difficulty=?,
            reward=?, duration_est=?, deadline=?, guidelines=?, updated_at=?
            WHERE id=?
        ''', (title, description, category, language, difficulty, reward,
              duration_est, deadline, guidelines,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id))

        conn.execute(
            "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
            (task_id, 'Admin', 'edited', f'Task details updated by admin — new earnings ${reward:.2f}')
        )
        conn.commit()
        conn.close()
        flash('Task updated successfully.', 'success')
        return redirect(url_for('task_detail', task_id=task_id))

    conn.close()
    return render_template('edit_task.html', task=task)


@app.route('/tasks/<int:task_id>/delete', methods=['POST'])
@require_admin
def delete_task(task_id):
    conn = database.get_db_connection()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if task and task['video_type'] == 'upload' and task['video_filename']:
        fp = os.path.join(app.config['UPLOAD_FOLDER'], task['video_filename'])
        if os.path.exists(fp):
            os.remove(fp)
    conn.execute('DELETE FROM transcriptions WHERE task_id = ?', (task_id,))
    conn.execute('DELETE FROM task_activities WHERE task_id = ?', (task_id,))
    conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    flash('Task deleted permanently.', 'info')
    return redirect(url_for('admin_dashboard'))


# ─── Task Detail Page (Public Viewing) ────────────────────────────────────────

@app.route('/tasks/<int:task_id>')
def task_detail(task_id):
    conn  = database.get_db_connection()
    task  = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        flash('Task not found.', 'error')
        return redirect(url_for('index'))

    activities     = conn.execute(
        'SELECT * FROM task_activities WHERE task_id = ? ORDER BY created_at DESC',
        (task_id,)
    ).fetchall()
    transcription  = conn.execute(
        'SELECT * FROM transcriptions WHERE task_id = ?', (task_id,)
    ).fetchone()
    conn.close()

    user = get_current_user()
    return render_template('task_detail.html',
                           task=task,
                           activities=activities,
                           transcription=transcription,
                           user=user)


# ─── Claim Task (Requires Login + Entry Fee Payment) ──────────────────────────

@app.route('/tasks/<int:task_id>/claim', methods=['POST'])
@require_paid_worker
def claim_task(task_id):
    user = get_current_user()

    conn = database.get_db_connection()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if not task or task['status'] != 'open':
        conn.close()
        flash('This task is no longer available.', 'warning')
        return redirect(url_for('index'))

    now = datetime.now()
    conn.execute('''
        UPDATE tasks SET status='claimed', claimed_by=?, claimer_nickname=?,
        claimed_at=?, deadline_claimed=?, updated_at=?
        WHERE id=?
    ''', (user['id'], user['full_name'], now.strftime("%Y-%m-%d %H:%M:%S"),
          (now + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S"),
          now.strftime("%Y-%m-%d %H:%M:%S"), task_id))

    conn.execute('''
        INSERT OR REPLACE INTO transcriptions
          (task_id, transcriber_id, transcriber_nickname, content_text, content_json, status)
        VALUES (?, ?, ?, '', '[]', 'draft')
    ''', (task_id, user['id'], user['full_name']))

    conn.execute(
        "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
        (task_id, user['full_name'], 'claimed', f'{user["full_name"]} claimed this task')
    )
    conn.commit()
    conn.close()

    flash(f'Task claimed! You have 48 hours to complete this transcription, {user["full_name"]}.', 'success')
    return redirect(url_for('studio', task_id=task_id))


@app.route('/tasks/<int:task_id>/release', methods=['POST'])
def release_task(task_id):
    user = get_current_user()
    conn = database.get_db_connection()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()

    if not task:
        conn.close()
        flash('Task not found.', 'error')
        return redirect(url_for('index'))

    is_claimer = user and task['claimed_by'] == user['id']

    if not is_admin() and not is_claimer:
        conn.close()
        flash('You are not authorized to release this task.', 'error')
        return redirect(url_for('task_detail', task_id=task_id))

    actor_name = user['full_name'] if is_claimer else 'Admin'
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute('''
        UPDATE tasks SET status='open', claimed_by=NULL, claimer_nickname=NULL,
        claimed_at=NULL, deadline_claimed=NULL, updated_at=?
        WHERE id=?
    ''', (now, task_id))
    conn.execute(
        "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
        (task_id, actor_name, 'released', 'Task released back to the marketplace')
    )
    conn.commit()
    conn.close()
    flash('Task released back to the marketplace.', 'info')
    return redirect(url_for('task_detail', task_id=task_id))


# ─── Transcription Studio ─────────────────────────────────────────────────────

@app.route('/tasks/<int:task_id>/studio')
def studio(task_id):
    user = get_current_user()
    conn = database.get_db_connection()
    task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        flash('Task not found.', 'error')
        return redirect(url_for('index'))

    if not is_admin():
        if not user or not user.get('is_paid') or task['claimed_by'] != user['id']:
            conn.close()
            flash('You must claim this task with an active account to enter the studio.', 'warning')
            return redirect(url_for('task_detail', task_id=task_id))

    transcription = conn.execute(
        'SELECT * FROM transcriptions WHERE task_id = ?', (task_id,)
    ).fetchone()
    conn.close()

    return render_template('studio.html', task=task, transcription=transcription)


@app.route('/api/tasks/<int:task_id>/save-draft', methods=['POST'])
def save_draft(task_id):
    data         = request.get_json() or {}
    content_text = data.get('content_text', '')
    content_json = data.get('content_json', '[]')
    notes        = data.get('notes', '')
    now          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = database.get_db_connection()
    existing = conn.execute('SELECT id FROM transcriptions WHERE task_id = ?', (task_id,)).fetchone()
    if existing:
        conn.execute('''
            UPDATE transcriptions
            SET content_text=?, content_json=?, transcriber_notes=?, updated_at=?
            WHERE task_id=?
        ''', (content_text, content_json, notes, now, task_id))
    else:
        user = get_current_user()
        u_id, u_name = (user['id'], user['full_name']) if user else (None, 'Transcriber')
        conn.execute('''
            INSERT INTO transcriptions
              (task_id, transcriber_id, transcriber_nickname, content_text, content_json, status, transcriber_notes)
            VALUES (?, ?, ?, ?, ?, 'draft', ?)
        ''', (task_id, u_id, u_name, content_text, content_json, notes))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'saved_at': now})


@app.route('/api/tasks/<int:task_id>/submit', methods=['POST'])
def submit_transcription(task_id):
    data         = request.get_json() or {}
    content_text = data.get('content_text', '')
    content_json = data.get('content_json', '[]')
    notes        = data.get('notes', '')
    now          = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = database.get_db_connection()
    conn.execute('''
        UPDATE transcriptions
        SET content_text=?, content_json=?, status='submitted',
            transcriber_notes=?, submitted_at=?, updated_at=?
        WHERE task_id=?
    ''', (content_text, content_json, notes, now, now, task_id))
    conn.execute("UPDATE tasks SET status='submitted', updated_at=? WHERE id=?", (now, task_id))

    user = get_current_user()
    actor_name = user['full_name'] if user else 'Transcriber'
    conn.execute(
        "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
        (task_id, actor_name, 'submitted', f'{actor_name} submitted transcription for review')
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'redirect_url': url_for('task_detail', task_id=task_id)})


# ─── Admin Review ─────────────────────────────────────────────────────────────

@app.route('/tasks/<int:task_id>/review', methods=['GET', 'POST'])
@require_admin
def review_task(task_id):
    conn  = database.get_db_connection()
    task  = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
    if not task:
        conn.close()
        flash('Task not found.', 'error')
        return redirect(url_for('admin_dashboard'))

    transcription = conn.execute(
        'SELECT * FROM transcriptions WHERE task_id = ?', (task_id,)
    ).fetchone()

    if request.method == 'POST':
        action   = request.form.get('action')
        feedback = request.form.get('feedback', '').strip()
        now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if action == 'approve':
            conn.execute("UPDATE tasks SET status='completed', updated_at=? WHERE id=?", (now, task_id))
            conn.execute('''
                UPDATE transcriptions SET status='approved', reviewer_feedback=?,
                reviewed_at=?, updated_at=? WHERE task_id=?
            ''', (feedback, now, now, task_id))

            if task['claimed_by']:
                conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (task['reward'], task['claimed_by'])
                )

            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (task_id, 'Admin', 'approved',
                 f'Transcription approved — ${task["reward"]:.2f} earnings released')
            )
            conn.commit()
            conn.close()
            flash(f'Transcription approved! ${task["reward"]:.2f} credited to transcriber.', 'success')
            return redirect(url_for('review_task', task_id=task_id))

        elif action == 'revision':
            conn.execute("UPDATE tasks SET status='claimed', updated_at=? WHERE id=?", (now, task_id))
            conn.execute('''
                UPDATE transcriptions SET status='revision_requested', reviewer_feedback=?,
                reviewed_at=?, updated_at=? WHERE task_id=?
            ''', (feedback, now, now, task_id))
            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (task_id, 'Admin', 'revision_requested', f'Revision requested: {feedback}')
            )
            conn.commit()
            conn.close()
            flash('Revision requested. The transcriber will see your feedback.', 'info')
            return redirect(url_for('review_task', task_id=task_id))

        elif action == 'reject':
            conn.execute(
                "UPDATE tasks SET status='open', claimed_by=NULL, claimer_nickname=NULL, updated_at=? WHERE id=?",
                (now, task_id)
            )
            conn.execute('''
                UPDATE transcriptions SET status='rejected', reviewer_feedback=?,
                reviewed_at=?, updated_at=? WHERE task_id=?
            ''', (feedback, now, now, task_id))
            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (task_id, 'Admin', 'rejected', f'Submission rejected. Task reopened.')
            )
            conn.commit()
            conn.close()
            flash('Submission rejected. Task reopened to the public.', 'warning')
            return redirect(url_for('task_detail', task_id=task_id))

    segments = exporter.parse_segments(transcription['content_json'] if transcription else '')
    conn.close()
    return render_template('review.html', task=task, transcription=transcription, segments=segments)


# ─── Export / Download ────────────────────────────────────────────────────────

@app.route('/tasks/<int:task_id>/export/<format_type>')
def export_transcription(task_id, format_type):
    conn  = database.get_db_connection()
    task  = conn.execute('SELECT title FROM tasks WHERE id = ?', (task_id,)).fetchone()
    tr    = conn.execute('SELECT * FROM transcriptions WHERE task_id = ?', (task_id,)).fetchone()
    conn.close()

    if not tr:
        flash('No transcription available to export.', 'error')
        return redirect(url_for('task_detail', task_id=task_id))

    clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', (task['title'] if task else 'transcript'))[:40]
    cj    = tr['content_json'] or ''
    fb    = tr['content_text']  or ''

    if format_type == 'srt':
        data, mime, ext = exporter.generate_srt(cj, fb), 'text/plain', 'srt'
    elif format_type == 'vtt':
        data, mime, ext = exporter.generate_vtt(cj, fb), 'text/vtt', 'vtt'
    elif format_type == 'json':
        data, mime, ext = (cj or '[]'), 'application/json', 'json'
    else:
        data, mime, ext = exporter.generate_txt(cj, fb), 'text/plain', 'txt'

    return Response(
        data, mimetype=mime,
        headers={'Content-Disposition': f'attachment;filename={clean}.{ext}'}
    )


# ─── Guidelines Page ─────────────────────────────────────────────────────────

@app.route('/guidelines')
def guidelines():
    return render_template('guidelines.html')


# ─── File Serving ─────────────────────────────────────────────────────────────

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, conditional=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 TranscribeHub running on http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
