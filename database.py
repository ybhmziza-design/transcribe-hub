"""
Database schema, connection helper, and settings manager for TranscribeHub.
Supports both PostgreSQL (via DATABASE_URL on Render) and SQLite (local development).
"""
import os
import hashlib
import uuid
import re
from datetime import datetime, timedelta

# ─── Default Credentials & Defaults ──────────────────────────────────────────
DEFAULT_ADMIN_USERNAME = "tanuialpha2@gmail.com"
DEFAULT_ADMIN_PASSWORD = "1234"
DEFAULT_ENTRY_FEE_AMOUNT = 573
DEFAULT_ENTRY_FEE_CURRENCY = "KES"
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'transcribe.db')
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def is_postgres() -> bool:
    return bool(DATABASE_URL)


class PgRow(dict):
    """Dict subclass allowing both dictionary and attribute-style access for rows."""
    def __getitem__(self, item):
        if isinstance(item, int):
            return list(self.values())[item]
        return super().__getitem__(item)


class PgCursorWrapper:
    """Wraps a psycopg2 cursor so that ? parameter placeholders are translated to %s."""
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        clean_q = query
        if '?' in clean_q:
            clean_q = re.sub(r'\?', '%s', clean_q)
        if params is not None:
            if isinstance(params, tuple):
                params = list(params)
            self._cursor.execute(clean_q, params)
        else:
            self._cursor.execute(clean_q)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgRow(row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [PgRow(r) for r in rows]

    @property
    def rowcount(self):
        return self._cursor.rowcount


class PgConnectionWrapper:
    """Wraps a psycopg2 connection to mimic sqlite3 connection semantics."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        import psycopg2.extras
        return PgCursorWrapper(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db_connection():
    if is_postgres():
        import psycopg2
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        return PgConnectionWrapper(conn)
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    if is_postgres():
        # ── PostgreSQL Schema ──────────────────────────────────────────────────
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin (
            id       SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id              SERIAL PRIMARY KEY,
            username        TEXT UNIQUE NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password        TEXT NOT NULL,
            full_name       TEXT NOT NULL,
            phone           TEXT,
            payout_details  TEXT,
            is_paid         INTEGER DEFAULT 0,
            paid_at         TIMESTAMP,
            payment_ref     TEXT,
            balance         REAL DEFAULT 0.0,
            rating          REAL DEFAULT 5.0,
            terms_agreed    INTEGER DEFAULT 0,
            terms_agreed_at TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            amount          REAL NOT NULL,
            currency        TEXT DEFAULT 'KES',
            payment_method  TEXT DEFAULT 'paystack',
            transaction_ref TEXT UNIQUE NOT NULL,
            paystack_status TEXT DEFAULT 'pending',
            phone_number    TEXT,
            status          TEXT NOT NULL DEFAULT 'completed',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id               SERIAL PRIMARY KEY,
            title            TEXT NOT NULL,
            description      TEXT,
            category         TEXT NOT NULL DEFAULT 'General',
            language         TEXT NOT NULL DEFAULT 'English (US)',
            difficulty       TEXT NOT NULL DEFAULT 'Medium',
            reward           REAL NOT NULL DEFAULT 15.0,
            duration_est     TEXT DEFAULT '05:00',
            deadline         TEXT,
            guidelines       TEXT,
            video_type       TEXT NOT NULL DEFAULT 'upload',
            video_source     TEXT NOT NULL,
            video_filename   TEXT,
            status           TEXT NOT NULL DEFAULT 'open',
            claimed_by       INTEGER REFERENCES users(id),
            claimer_nickname TEXT,
            claimed_at       TIMESTAMP,
            deadline_claimed TIMESTAMP,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS transcriptions (
            id                   SERIAL PRIMARY KEY,
            task_id              INTEGER NOT NULL UNIQUE REFERENCES tasks(id),
            transcriber_id       INTEGER REFERENCES users(id),
            transcriber_nickname TEXT,
            content_text         TEXT DEFAULT '',
            content_json         TEXT DEFAULT '[]',
            status               TEXT NOT NULL DEFAULT 'draft',
            transcriber_notes    TEXT,
            reviewer_feedback    TEXT,
            submitted_at         TIMESTAMP,
            reviewed_at          TIMESTAMP,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_activities (
            id         SERIAL PRIMARY KEY,
            task_id    INTEGER NOT NULL REFERENCES tasks(id),
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            message    TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_resets (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            token      TEXT UNIQUE NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used       INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

    else:
        # ── SQLite Schema ──────────────────────────────────────────────────────
        import sqlite3
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    UNIQUE NOT NULL,
            password TEXT    NOT NULL
        )
        ''')

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
        ''')

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

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN terms_agreed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN terms_agreed_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass

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
    row = cursor.fetchone()
    admin_count = row[0] if row else 0
    if admin_count == 0:
        cursor.execute(
            'INSERT INTO admin (username, password) VALUES (?, ?)',
            (DEFAULT_ADMIN_USERNAME, _hash(DEFAULT_ADMIN_PASSWORD))
        )
        conn.commit()

    # Seed default settings if not present
    default_settings = {
        'entry_fee_amount': str(DEFAULT_ENTRY_FEE_AMOUNT),
        'entry_fee_currency': DEFAULT_ENTRY_FEE_CURRENCY,
        'paystack_public_key': os.environ.get('PAYSTACK_PUBLIC_KEY', ''),
        'paystack_secret_key': os.environ.get('PAYSTACK_SECRET_KEY', ''),
        'r2_public_url': os.environ.get('R2_PUBLIC_URL', 'https://pub-5befd43eda4542d3bd6060bc91f59062.r2.dev'),
        'r2_endpoint_url': os.environ.get('R2_ENDPOINT_URL', 'https://12ced18125a43e40810d5f9b82ad8ae2.r2.cloudflarestorage.com'),
        'r2_bucket_name': os.environ.get('R2_BUCKET_NAME', 'transcribe-tasks'),
        'r2_access_key_id': os.environ.get('R2_ACCESS_KEY_ID', ''),
        'r2_secret_access_key': os.environ.get('R2_SECRET_ACCESS_KEY', ''),
        'ai_auto_review_enabled': '1',
        'ai_pass_threshold': '88',
        'ai_whisper_model': 'tiny',
        'paypal_client_id': '',
        'paypal_client_secret': '',
        'paypal_entry_fee_usd': '5.00',
    }
    for k, v in default_settings.items():
        existing = cursor.execute('SELECT key FROM settings WHERE key = ?', (k,)).fetchone()
        if not existing:
            cursor.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (k, v))
        elif not existing.get('value'):
            cursor.execute('UPDATE settings SET value = ? WHERE key = ?', (v, k))
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
    row = conn.execute('SELECT key FROM settings WHERE key = ?', (key,)).fetchone()
    if row:
        conn.execute('UPDATE settings SET value = ? WHERE key = ?', (value, key))
    else:
        conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


def get_entry_fee() -> tuple[int, str]:
    amount = int(float(get_setting('entry_fee_amount', str(DEFAULT_ENTRY_FEE_AMOUNT))))
    currency = get_setting('entry_fee_currency', DEFAULT_ENTRY_FEE_CURRENCY)
    return amount, currency


# ─── Admin Auth Helpers ───────────────────────────────────────────────────────

def verify_admin(username: str, password: str) -> bool:
    conn = get_db_connection()
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


# ─── Worker Performance Tier ──────────────────────────────────────────────────

def get_worker_performance(user_id: int) -> dict:
    conn = get_db_connection()
    row = conn.execute('''
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('approved', 'ai_approved') THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected
        FROM transcriptions
        WHERE transcriber_id = ? AND status NOT IN ('draft', 'submitted', 'revision_requested')
    ''', (user_id,)).fetchone()
    conn.close()

    total    = row['total']    if row else 0
    approved = (row['approved'] or 0) if row else 0
    rejected = (row['rejected'] or 0) if row else 0
    rate     = (approved / total * 100) if total > 0 else 0.0

    if total == 0 or approved == 0:
        tier, max_tasks, color = 'Starter', 1, 'slate'
    elif rate >= 90:
        tier, max_tasks, color = 'Expert',  5, 'emerald'
    elif rate >= 75:
        tier, max_tasks, color = 'Senior',  3, 'blue'
    elif rate >= 60:
        tier, max_tasks, color = 'Worker',  2, 'amber'
    else:
        tier, max_tasks, color = 'Starter', 1, 'slate'

    return {
        'total_submitted': total,
        'approved': approved,
        'rejected': rejected,
        'approval_rate': round(rate, 1),
        'tier': tier,
        'max_tasks': max_tasks,
        'tier_color': color,
    }


def get_worker_active_task_count(user_id: int) -> int:
    conn = get_db_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE claimed_by = ? AND status = 'claimed'",
        (user_id,)
    ).fetchone()
    conn.close()
    return row['cnt'] if row else 0


# ─── Payment Recording ─────────────────────────────────────────────────────────

def record_payment(user_id: int, amount: float, currency: str, transaction_ref: str, payment_method: str = "paystack", phone: str = "", paystack_status: str = "success") -> str:
    conn = get_db_connection()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Portable Upsert for payments record
    existing_p = conn.execute('SELECT id FROM payments WHERE transaction_ref = ?', (transaction_ref,)).fetchone()
    if existing_p:
        conn.execute('''
            UPDATE payments
            SET user_id = ?, amount = ?, currency = ?, payment_method = ?, paystack_status = ?, phone_number = ?, status = 'completed'
            WHERE transaction_ref = ?
        ''', (user_id, amount, currency, payment_method, paystack_status, phone, transaction_ref))
    else:
        conn.execute('''
            INSERT INTO payments (user_id, amount, currency, payment_method, transaction_ref, paystack_status, phone_number, status, created_at)
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
    conn = get_db_connection()
    user = conn.execute(
        'SELECT id FROM users WHERE email = ?', (email.lower().strip(),)
    ).fetchone()
    if not user:
        conn.close()
        return True, "__no_user__"

    conn.execute(
        'UPDATE password_resets SET used = 1 WHERE user_id = ? AND used = 0',
        (user['id'],)
    )

    token = uuid.uuid4().hex + uuid.uuid4().hex
    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        'INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)',
        (user['id'], token, expires_at)
    )
    conn.commit()
    conn.close()
    return True, token


def verify_reset_token(token: str):
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

    expires_at = row['expires_at']
    if isinstance(expires_at, str):
        expires_at = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    if datetime.now() > expires_at:
        conn.close()
        return None, None

    user = conn.execute('SELECT * FROM users WHERE id = ?', (row['user_id'],)).fetchone()
    reset_id = row['reset_id']
    conn.close()
    return (dict(user) if user else None), reset_id


def consume_reset_token(reset_id: int, user_id: int, new_password: str):
    conn = get_db_connection()
    conn.execute('UPDATE password_resets SET used = 1 WHERE id = ?', (reset_id,))
    conn.execute('UPDATE users SET password = ? WHERE id = ?', (_hash(new_password), user_id))
    conn.commit()
    conn.close()
