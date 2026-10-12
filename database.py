"""
Database schema, connection helper, and settings manager for TranscribeHub.
Single-admin model with Paystack entry fee integration.
"""
import sqlite3
import os
import hashlib
import uuid
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'transcribe.db')

# ─── Default Credentials & Defaults ──────────────────────────────────────────
DEFAULT_ADMIN_USERNAME = "tanuialpha2@gmail.com"
DEFAULT_ADMIN_PASSWORD = "1234"
DEFAULT_ENTRY_FEE_AMOUNT = 573
DEFAULT_ENTRY_FEE_CURRENCY = "KES"
# ──────────────────────────────────────────────────────────────────────────────


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # ── Admin table ────────────────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT    UNIQUE NOT NULL,
        password TEXT    NOT NULL
    )
    ''')

    # ── Platform Settings table (Paystack keys, entry fee amount/currency) ─────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    ''')

    # ── Users / Transcribers table ─────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        username       TEXT    UNIQUE NOT NULL,
        email          TEXT    UNIQUE NOT NULL,
        password       TEXT    NOT NULL,
        full_name      TEXT    NOT NULL,
        phone          TEXT,
        payout_details TEXT,
        is_paid        INTEGER DEFAULT 0,
        paid_at        TIMESTAMP,
        payment_ref    TEXT,
        balance        REAL    DEFAULT 0.0,
        rating         REAL    DEFAULT 5.0,
        terms_agreed   INTEGER DEFAULT 0,
        terms_agreed_at TIMESTAMP,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Migration for existing users table
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN terms_agreed INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN terms_agreed_at TIMESTAMP")
    except sqlite3.OperationalError:
        pass

    # ── Payments table (records Paystack transactions) ─────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        amount          REAL    NOT NULL,
        currency        TEXT    DEFAULT 'KES',
        payment_method  TEXT    DEFAULT 'paystack',
        transaction_ref TEXT    UNIQUE NOT NULL,
        paystack_status TEXT    DEFAULT 'pending',
        phone_number    TEXT,
        status          TEXT    NOT NULL DEFAULT 'completed',
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    # ── Tasks ─────────────────────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tasks (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        title            TEXT    NOT NULL,
        description      TEXT,
        category         TEXT    NOT NULL DEFAULT 'General',
        language         TEXT    NOT NULL DEFAULT 'English (US)',
        difficulty       TEXT    NOT NULL DEFAULT 'Medium',
        reward           REAL    NOT NULL DEFAULT 15.0,
        duration_est     TEXT    DEFAULT '05:00',
        deadline         TEXT,
        guidelines       TEXT,
        video_type       TEXT    NOT NULL DEFAULT 'upload',
        video_source     TEXT    NOT NULL,
        video_filename   TEXT,
        status           TEXT    NOT NULL DEFAULT 'open',
        claimed_by       INTEGER,
        claimer_nickname TEXT,
        claimed_at       TIMESTAMP,
        deadline_claimed TIMESTAMP,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (claimed_by) REFERENCES users (id)
    )
    ''')

    # ── Transcriptions ────────────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transcriptions (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id            INTEGER NOT NULL UNIQUE,
        transcriber_id     INTEGER,
        transcriber_nickname TEXT,
        content_text       TEXT    DEFAULT '',
        content_json       TEXT    DEFAULT '[]',
        status             TEXT    NOT NULL DEFAULT 'draft',
        transcriber_notes  TEXT,
        reviewer_feedback  TEXT,
        submitted_at       TIMESTAMP,
        reviewed_at        TIMESTAMP,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks (id),
        FOREIGN KEY (transcriber_id) REFERENCES users (id)
    )
    ''')

    # ── Activity log ──────────────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS task_activities (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id    INTEGER NOT NULL,
        actor      TEXT    NOT NULL,
        action     TEXT    NOT NULL,
        message    TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (task_id) REFERENCES tasks (id)
    )
    ''')

    # ── Password Reset Tokens ─────────────────────────────────────────────────
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS password_resets (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        token      TEXT    UNIQUE NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used       INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    ''')

    conn.commit()

    # Seed admin account if not present
    cursor.execute('SELECT COUNT(*) FROM admin')
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            'INSERT INTO admin (username, password) VALUES (?, ?)',
            (DEFAULT_ADMIN_USERNAME, _hash(DEFAULT_ADMIN_PASSWORD))
        )
        conn.commit()

    # Seed default settings if not present
    default_settings = {
        'entry_fee_amount': str(DEFAULT_ENTRY_FEE_AMOUNT),
        'entry_fee_currency': DEFAULT_ENTRY_FEE_CURRENCY,
        'paystack_public_key': os.environ.get('PAYSTACK_PUBLIC_KEY', 'pk_test_sample_key_replace_with_yours'),
        'paystack_secret_key': os.environ.get('PAYSTACK_SECRET_KEY', 'sk_test_sample_key_replace_with_yours'),
        'ai_auto_review_enabled': '1',
        'ai_pass_threshold': '88',
        'ai_whisper_model': 'tiny'
    }
    for k, v in default_settings.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))
    conn.commit()

    conn.close()


# ─── Settings Accessors ────────────────────────────────────────────────────────

def get_setting(key: str, default: str = "") -> str:
    conn = get_db_connection()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    if row and row['value'] is not None:
        return row['value']
    return default


def set_setting(key: str, value: str):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


def get_entry_fee() -> tuple[int, str]:
    amount = int(float(get_setting('entry_fee_amount', str(DEFAULT_ENTRY_FEE_AMOUNT))))
    currency = get_setting('entry_fee_currency', DEFAULT_ENTRY_FEE_CURRENCY)
    return amount, currency


# ─── Admin Auth Helpers ───────────────────────────────────────────────────────

def verify_admin(username: str, password: str) -> bool:
    conn = get_db_connection()
    # Case-insensitive match — store and compare lowercased
    row = conn.execute(
        'SELECT password FROM admin WHERE LOWER(username) = ?', (username.strip().lower(),)
    ).fetchone()
    conn.close()
    if not row:
        return False
    return row['password'] == _hash(password)


def change_admin_password(new_password: str):
    conn = get_db_connection()
    conn.execute(
        'UPDATE admin SET password = ? WHERE id = 1',
        (_hash(new_password),)
    )
    conn.commit()
    conn.close()


# ─── User Accounts ─────────────────────────────────────────────────────────────

def create_user(username: str, email: str, password: str, full_name: str, phone: str = "", payout_details: str = "", terms_agreed: int = 1) -> tuple[bool, str]:
    conn = get_db_connection()
    try:
        existing = conn.execute(
            'SELECT id FROM users WHERE username = ? OR email = ?',
            (username.lower().strip(), email.lower().strip())
        ).fetchone()
        if existing:
            conn.close()
            return False, "Username or Email already registered."

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if terms_agreed else None
        conn.execute('''
            INSERT INTO users (username, email, password, full_name, phone, payout_details, terms_agreed, terms_agreed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            username.lower().strip(),
            email.lower().strip(),
            _hash(password),
            full_name.strip(),
            phone.strip(),
            payout_details.strip(),
            1 if terms_agreed else 0,
            now
        ))
        conn.commit()
        conn.close()
        return True, "Account created successfully!"
    except Exception as e:
        conn.close()
        return False, str(e)


def agree_to_terms(user_id: int):
    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute('UPDATE users SET terms_agreed = 1, terms_agreed_at = ? WHERE id = ?', (now, user_id))
    conn.commit()
    conn.close()


def authenticate_user(username_or_email: str, password: str):
    conn = get_db_connection()
    login_id = username_or_email.lower().strip()
    user = conn.execute('''
        SELECT * FROM users
        WHERE (username = ? OR email = ?) AND password = ?
    ''', (login_id, login_id, _hash(password))).fetchone()
    conn.close()
    return dict(user) if user else None


def get_user_by_id(user_id: int):
    if not user_id:
        return None
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


# ─── Payment Recording ─────────────────────────────────────────────────────────

def record_payment(user_id: int, amount: float, currency: str, transaction_ref: str, payment_method: str = "paystack", phone: str = "", paystack_status: str = "success") -> str:
    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Record or update payment record
    conn.execute('''
        INSERT OR REPLACE INTO payments (user_id, amount, currency, payment_method, transaction_ref, paystack_status, phone_number, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)
    ''', (user_id, amount, currency, payment_method, transaction_ref, paystack_status, phone, now))

    # Mark user as paid & activated
    conn.execute('''
        UPDATE users
        SET is_paid = 1, paid_at = ?, payment_ref = ?
        WHERE id = ?
    ''', (now, transaction_ref, user_id))

    conn.commit()
    conn.close()
    return transaction_ref


# ─── Password Reset Helpers ────────────────────────────────────────────────────

def create_reset_token(email: str) -> tuple[bool, str]:
    """
    Creates a one-time password reset token for the given email.
    Returns (True, token) on success, or (False, error_message).
    Token expires in 1 hour.
    """
    conn = get_db_connection()
    user = conn.execute(
        'SELECT id FROM users WHERE email = ?', (email.lower().strip(),)
    ).fetchone()
    if not user:
        conn.close()
        # Return success to avoid leaking whether an email is registered
        return True, "__no_user__"

    # Expire any old unused tokens for this user
    conn.execute(
        'UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0',
        (user['id'],)
    )

    token = uuid.uuid4().hex + uuid.uuid4().hex  # 64-char hex token
    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        'INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)',
        (user['id'], token, expires_at)
    )
    conn.commit()
    conn.close()
    return True, token


def verify_reset_token(token: str):
    """
    Validates a reset token. Returns the (user dict, reset_id) if valid and not expired,
    or (None, None) if invalid / expired / already used.
    """
    conn = get_db_connection()
    row = conn.execute(
        '''SELECT pr.id AS reset_id, pr.user_id, pr.expires_at, pr.used
           FROM password_resets pr
           WHERE pr.token = ?''',
        (token,)
    ).fetchone()

    if not row:
        conn.close()
        return None, None

    if row['used']:
        conn.close()
        return None, None

    # Check expiry
    expires_at = datetime.strptime(row['expires_at'], "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expires_at:
        conn.close()
        return None, None

    user = conn.execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone()
    reset_id = row['reset_id']
    conn.close()
    return (dict(user) if user else None), reset_id


def consume_reset_token(reset_id: int, user_id: int, new_password: str):
    """Marks the token as used and updates the user's password."""
    conn = get_db_connection()
    conn.execute('UPDATE password_resets SET used = 1 WHERE id = ?', (reset_id,))
    conn.execute('UPDATE users SET password = ? WHERE id = ?', (_hash(new_password), user_id))
    conn.commit()
    conn.close()
