from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import numpy as np
import joblib
import tensorflow as tf
import sqlite3
import os
import time
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from datetime import datetime, timedelta
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib
import uuid
import random
import string
import re
import sys
import subprocess

# ── V3.0: make scripts/ importable ────────────────────────────────────────
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Graceful imports for V3.0 helper modules
try:
    from nlp_scorer import get_nlp_score
except Exception as _e:
    print(f'[V3.0] nlp_scorer unavailable: {_e}')
    def get_nlp_score(r): return 0.0  # noqa

try:
    from graph_detector import get_ring_score, get_ring_detail
except Exception as _e:
    print(f'[V3.0] graph_detector unavailable: {_e}')
    def get_ring_score(u, db_path='database.db'): return 0.0  # noqa
    def get_ring_detail(u, db_path='database.db'):  # noqa
        return {'ring_detected': False, 'ring_score': 0.0,
                'distinct_ips': 0, 'distinct_users': 0}

app = Flask(__name__)

# Load environment variables from .env file
load_dotenv()

XGB_MODEL_PATH = os.path.join(app.root_path, 'model', 'xgb_model.json')
XGBOOST_AVAILABLE = False
xgb = None
xgb_model = None
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    pass

app.secret_key = 'super-secret-key'

MODEL_DIR = os.path.join(app.root_path, 'model')
UPLOAD_DIR = os.path.join(app.root_path, 'uploads')
DL_MODEL_PATH = os.path.join(MODEL_DIR, 'dl_model.h5')
RF_MODEL_PATH = os.path.join(MODEL_DIR, 'rf_model.pkl')
SCALER_PATH = os.path.join(MODEL_DIR, 'scaler.pkl')

dl_model = None
rf_model = None
scaler = None
models_loaded = False
model_accuracy = None


def create_dummy_dataset(size=2000):
    """Generate realistic UPI transaction dataset covering ₹10 to ₹200,000.

    Fraud logic: only truly suspicious combinations are labelled fraud —
    extreme amounts (>₹100,000) in risky categories, or very small probe
    amounts (<₹15) in risky categories.  Normal payments of any size in
    safe categories (payment, refund, deposit, upi) are labelled safe.
    """
    np.random.seed(42)
    # Realistic amount distribution: mix of small daily txns and large ones
    amounts = np.concatenate([
        np.random.uniform(10, 500, size // 4),       # small daily txns
        np.random.uniform(500, 5000, size // 4),      # medium txns
        np.random.uniform(5000, 50000, size // 4),    # large txns
        np.random.uniform(50000, 200000, size // 4),  # very large txns
    ])
    np.random.shuffle(amounts)
    amounts = amounts[:size]
    categories = np.random.randint(0, 7, size)
    X = np.column_stack((amounts, categories))
    # Fraud = extreme amount (>100K) AND risky category, OR tiny probe (<15) in risky cat
    risky = np.isin(categories, [0, 3, 6])  # transfer, withdrawal, p2p
    y = (
        (risky & (amounts > 100000)) |          # very large + risky category
        (risky & (amounts < 15))                # tiny probe amount + risky category
    ).astype(int)
    return X, y


def build_dummy_models():
    os.makedirs(MODEL_DIR, exist_ok=True)
    X, y = create_dummy_dataset(2000)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    rf_model_local = RandomForestClassifier(n_estimators=100, random_state=42)
    rf_model_local.fit(X_scaled, y)

    dl_model_local = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(2,)),
        tf.keras.layers.Dense(32, activation='relu'),
        tf.keras.layers.Dense(16, activation='relu'),
        tf.keras.layers.Dense(8, activation='relu'),
        tf.keras.layers.Dense(1, activation='sigmoid'),
    ])
    dl_model_local.compile(optimizer='adam', loss='binary_crossentropy',
                           metrics=['accuracy'])
    dl_model_local.fit(X_scaled, y, epochs=40, batch_size=32, verbose=0)

    dl_model_local.save(DL_MODEL_PATH)
    joblib.dump(rf_model_local, RF_MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    if XGBOOST_AVAILABLE:
        xgb_model_local = xgb.XGBClassifier(use_label_encoder=False, eval_metric='logloss', random_state=42)
        xgb_model_local.fit(X_scaled, y)
        xgb_model_local.save_model(XGB_MODEL_PATH)
    print('ML models generated in model/ (trained on Rs.10-Rs.200K range)')


def compute_model_accuracy():
    global model_accuracy
    if dl_model is None or rf_model is None or scaler is None:
        return None

    X, y = create_dummy_dataset(500)
    X_scaled = scaler.transform(X)
    dl_preds = dl_model.predict(X_scaled).flatten()
    rf_preds = rf_model.predict(X_scaled).flatten()
    if XGBOOST_AVAILABLE and xgb_model is not None:
        xgb_preds = xgb_model.predict(X_scaled)
        hybrid_preds = ((0.5 * dl_preds) + (0.3 * rf_preds) + (0.2 * xgb_preds)) > 0.5
    else:
        hybrid_preds = ((0.6 * dl_preds) + (0.4 * rf_preds)) > 0.5
    model_accuracy = float(np.mean(hybrid_preds.astype(int) == y))
    return model_accuracy


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def ensure_column_exists(conn, table, column, definition):
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        # Remove UNIQUE constraint for ALTER TABLE as SQLite doesn't support it
        col_def = definition.replace('UNIQUE', '').strip()
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def load_models():
    global dl_model, rf_model, scaler, models_loaded, model_accuracy, xgb_model
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not (os.path.exists(DL_MODEL_PATH) and os.path.exists(RF_MODEL_PATH) and os.path.exists(SCALER_PATH)):
        build_dummy_models()

    try:
        dl_model = tf.keras.models.load_model(DL_MODEL_PATH)
        rf_model = joblib.load(RF_MODEL_PATH)
        scaler = joblib.load(SCALER_PATH)
        if XGBOOST_AVAILABLE and os.path.exists(XGB_MODEL_PATH):
            xgb_model = xgb.XGBClassifier(use_label_encoder=False, eval_metric='logloss')
            xgb_model.load_model(XGB_MODEL_PATH)
        models_loaded = True
        model_accuracy = compute_model_accuracy()
    except Exception as exc:
        print(f'Warning: could not load ML models: {exc}')
        models_loaded = False
        model_accuracy = None


def init_db():
    conn = sqlite3.connect('database.db')
    conn.execute(
        "CREATE TABLE IF NOT EXISTS logs ("
        "id INTEGER PRIMARY KEY, "
        "username TEXT, "
        "transaction_id TEXT, "
        "upi_id TEXT, "
        "payer_name TEXT, "
        "payee_name TEXT, "
        "remarks TEXT, "
        "screenshot TEXT, "
        "amount REAL, "
        "category TEXT, "
        "result TEXT, "
        "score REAL, "
        "error TEXT, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', is_verified INTEGER DEFAULT 0, otp TEXT, otp_expiry DATETIME, otp_attempts INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    ensure_column_exists(conn, 'logs', 'username', 'TEXT')
    ensure_column_exists(conn, 'logs', 'transaction_id', 'TEXT')
    ensure_column_exists(conn, 'logs', 'upi_id', 'TEXT')
    ensure_column_exists(conn, 'logs', 'payer_name', 'TEXT')
    ensure_column_exists(conn, 'logs', 'payee_name', 'TEXT')
    ensure_column_exists(conn, 'logs', 'remarks', 'TEXT')
    ensure_column_exists(conn, 'logs', 'screenshot', 'TEXT')
    ensure_column_exists(conn, 'logs', 'device_info', 'TEXT')
    ensure_column_exists(conn, 'logs', 'geo_location', 'TEXT')
    ensure_column_exists(conn, 'logs', 'ip_address', 'TEXT')
    ensure_column_exists(conn, 'users', 'email', 'TEXT UNIQUE')
    ensure_column_exists(conn, 'users', 'is_verified', 'INTEGER DEFAULT 0')
    ensure_column_exists(conn, 'users', 'otp', 'TEXT')
    ensure_column_exists(conn, 'users', 'otp_expiry', 'DATETIME')
    ensure_column_exists(conn, 'users', 'otp_attempts', 'INTEGER DEFAULT 0')
    # V3.0 new columns
    ensure_column_exists(conn, 'logs', 'confirmed_label',  'TEXT')
    ensure_column_exists(conn, 'logs', 'nlp_score',        'REAL')
    ensure_column_exists(conn, 'logs', 'velocity_score',   'REAL')
    ensure_column_exists(conn, 'logs', 'ring_score',       'REAL')
    conn.execute(
        'INSERT OR IGNORE INTO users(username, password_hash, role, is_verified) VALUES (?, ?, ?, ?)',
        ('admin', generate_password_hash('admin123'), 'admin', 1),
    )
    conn.commit()
    conn.close()


def parse_amount(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_category(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if value.replace('.', '', 1).isdigit():
        return float(value)
    categories = {
        'transfer': 0,
        'payment': 1,
        'refund': 2,
        'withdrawal': 3,
        'deposit': 4,
        'upi': 5,
        'p2p': 6,
    }
    return float(categories.get(value.lower(), -1))


def get_client_ip():
    """Get client IP address from request"""
    if request.environ.get('HTTP_CF_CONNECTING_IP'):
        return request.environ.get('HTTP_CF_CONNECTING_IP')
    return request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr).split(',')[0].strip()


def get_device_info():
    """Get device and browser information"""
    user_agent = request.headers.get('User-Agent', 'Unknown')
    device_type = 'Mobile' if any(x in user_agent for x in ['Mobile', 'Android', 'iPhone']) else 'Desktop'
    browser = 'Unknown'
    if 'Chrome' in user_agent:
        browser = 'Chrome'
    elif 'Firefox' in user_agent:
        browser = 'Firefox'
    elif 'Safari' in user_agent:
        browser = 'Safari'
    elif 'Edge' in user_agent:
        browser = 'Edge'
    return f"{device_type} • {browser}"


def get_geolocation(ip_address):
    """Get geolocation info from IP address using free API"""
    try:
        response = requests.get(f'https://ipapi.co/{ip_address}/json/', timeout=3)
        if response.status_code == 200:
            data = response.json()
            return f"{data.get('city', 'Unknown')}, {data.get('country_name', 'Unknown')}"
    except Exception as e:
        print(f"Geolocation lookup failed: {e}")
    return "Location: Unknown"


# ── V3.0 Feature 2: Velocity check ────────────────────────────────────────
def get_velocity_score(upi_id: str, ip_address: str, window_minutes: int = 15) -> float:
    """Count recent txns from the same UPI or IP and return a risk boost (0–0.25)."""
    cutoff = (datetime.utcnow() - timedelta(minutes=window_minutes)).isoformat()
    upi_count = ip_count = 0
    try:
        conn = sqlite3.connect('database.db')
        cur  = conn.cursor()
        if upi_id:
            cur.execute(
                'SELECT COUNT(*) FROM logs WHERE upi_id = ? AND created_at >= ?',
                (upi_id, cutoff)
            )
            upi_count = cur.fetchone()[0] or 0
        if ip_address:
            cur.execute(
                'SELECT COUNT(*) FROM logs WHERE ip_address = ? AND created_at >= ?',
                (ip_address, cutoff)
            )
            ip_count = cur.fetchone()[0] or 0
        conn.close()
    except Exception as exc:
        print(f'[velocity] DB error: {exc}')
    score = 0.0
    if upi_count >= 5:
        score += 0.12
    elif upi_count >= 3:
        score += 0.06
    if ip_count >= 3:
        score += 0.08
    elif ip_count >= 2:
        score += 0.04
    return round(min(0.25, score), 4)


# ── V3.0 Feature 2: Amount deviation ──────────────────────────────────────
def get_amount_deviation_score(username: str, current_amount: float) -> float:
    """Compare current amount vs user 30-day avg; return risk boost (0–0.12)."""
    if not username or current_amount is None:
        return 0.0
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    try:
        conn = sqlite3.connect('database.db')
        cur  = conn.cursor()
        cur.execute(
            'SELECT AVG(amount), COUNT(*) FROM logs '
            'WHERE username = ? AND created_at >= ? AND amount IS NOT NULL',
            (username, cutoff)
        )
        row = cur.fetchone()
        conn.close()
        avg_amount, count = row[0], row[1]
    except Exception as exc:
        print(f'[deviation] DB error: {exc}')
        return 0.0
    if not avg_amount or count < 3:
        return 0.0
    ratio = current_amount / avg_amount
    if ratio >= 20:   return 0.12
    if ratio >= 10:   return 0.08
    if ratio >= 5:    return 0.05
    return 0.0


def send_brevo_email(to_email, subject, html_content):
    """Send email using Brevo SMTP relay"""
    SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'noreply@securepay.local')
    SMTP_KEY = os.environ.get('BREVO_SMTP_KEY', '')
    SMTP_SERVER = os.environ.get('BREVO_SMTP_SERVER', 'smtp-relay.brevo.com')
    SMTP_PORT = int(os.environ.get('BREVO_SMTP_PORT', '587'))
    SMTP_LOGIN = os.environ.get('BREVO_SMTP_LOGIN', SENDER_EMAIL)
    
    if not SMTP_KEY or SMTP_KEY == 'your-brevo-api-key-here':
        print(f"[WARNING] Email not configured (missing BREVO_SMTP_KEY)")
        return False
    
    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = f"SecurePay AI <{SENDER_EMAIL}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(html_content, 'html'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_LOGIN, SMTP_KEY)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        
        print(f"[EMAIL] Successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send email: {e}")
        return False


def send_fraud_alert_email(username, transaction_data, score):
    """Send email alert to admins for high-risk transactions"""
    SMTP_KEY = os.environ.get('BREVO_SMTP_KEY', '')
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@securepay.local')
    
    if not SMTP_KEY or SMTP_KEY == 'your-brevo-api-key-here':
        print(f"Development Mode: Fraud alert email to {ADMIN_EMAIL} simulated.")
        return
    
    subject = f"🚨 High-Risk Transaction Alert: Score {score:.4f}"
    html_content = f"""
    <h2>🚨 HIGH-RISK TRANSACTION DETECTED</h2>
    <table style="border-collapse:collapse;">
        <tr><td><b>User:</b></td><td>{username}</td></tr>
        <tr><td><b>Transaction ID:</b></td><td>{transaction_data.get('transaction_id')}</td></tr>
        <tr><td><b>Risk Score:</b></td><td>{score:.4f}</td></tr>
        <tr><td><b>Amount:</b></td><td>₹{transaction_data.get('amount')}</td></tr>
        <tr><td><b>Category:</b></td><td>{transaction_data.get('category')}</td></tr>
        <tr><td><b>UPI ID:</b></td><td>{transaction_data.get('upi_id')}</td></tr>
        <tr><td><b>Device:</b></td><td>{transaction_data.get('device_info', 'Unknown')}</td></tr>
        <tr><td><b>Location:</b></td><td>{transaction_data.get('geo_location', 'Unknown')}</td></tr>
        <tr><td><b>IP:</b></td><td>{transaction_data.get('ip_address', 'Unknown')}</td></tr>
        <tr><td><b>Timestamp:</b></td><td>{datetime.now().isoformat()}</td></tr>
    </table>
    <p>Please review this transaction for potential fraud.</p>
    """
    send_brevo_email(ADMIN_EMAIL, subject, html_content)


def is_valid_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def generate_otp(length=6):
    """Generate random OTP"""
    return ''.join(random.choices(string.digits, k=length))


def send_otp_email(email, otp, username):
    """Send OTP to user email using Brevo API"""
    subject = "🔐 SecurePay AI - Your OTP for Email Verification"
    html_content = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #e0e0e0;border-radius:10px;">
        <h2 style="color:#1a73e8;text-align:center;">🔐 SecurePay AI</h2>
        <p>Hello {username},</p>
        <p>Your One-Time Password (OTP) for email verification is:</p>
        <div style="text-align:center;margin:20px 0;">
            <span style="font-size:32px;font-weight:bold;letter-spacing:8px;background:#f0f4ff;padding:15px 30px;border-radius:8px;color:#1a73e8;">{otp}</span>
        </div>
        <p>⏰ This OTP is valid for <b>5 minutes</b> only.</p>
        <p style="color:#666;">If you didn't request this OTP, please ignore this email.</p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">
        <p style="color:#999;font-size:12px;text-align:center;">SecurePay AI Team</p>
    </div>
    """
    return send_brevo_email(email, subject, html_content)


def send_welcome_email(email, username):
    """Send welcome email after successful verification"""
    subject = "✅ Welcome to SecurePay AI!"
    html_content = f"""
    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px;border:1px solid #e0e0e0;border-radius:10px;">
        <h2 style="color:#28a745;text-align:center;">✅ Welcome to SecurePay AI!</h2>
        <p>Hello <b>{username}</b>,</p>
        <p>Congratulations! Your account has been successfully verified and activated.</p>
        <p>You can now log in to SecurePay AI and start verifying your UPI transactions for fraud detection.</p>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:20px 0;">
        <p style="color:#999;font-size:12px;text-align:center;">SecurePay AI Team</p>
    </div>
    """
    return send_brevo_email(email, subject, html_content)


def get_user(username):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username, password_hash, role FROM users WHERE username = ?', (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {'username': row[0], 'password_hash': row[1], 'role': row[2]}


def create_user(username, password, role='user'):
    if get_user(username):
        return False
    password_hash = generate_password_hash(password)
    conn = sqlite3.connect('database.db')
    conn.execute('INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)', (username, password_hash, role))
    conn.commit()
    conn.close()
    return True


def get_all_users():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, email, role, is_verified, created_at FROM users ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [{'id': r[0], 'username': r[1], 'email': r[2] or '—', 'role': r[3], 'is_verified': bool(r[4]), 'created_at': r[5]} for r in rows]


def delete_user(username):
    if username == 'admin':
        return False
    conn = sqlite3.connect('database.db')
    conn.execute('DELETE FROM users WHERE username = ?', (username,))
    conn.commit()
    conn.close()
    return True

load_models()
init_db()

@app.route('/')
def landing():
    if 'user' in session:
        return redirect(url_for('unified'))
    return render_template('landing.html')


@app.route('/unified')
def unified():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('unified.html', session=session)


@app.route('/home')
def home():
    if 'user' not in session:
        return redirect(url_for('login', next='home'))
    return render_template('home.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    message = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = get_user(username)
        if user and check_password_hash(user['password_hash'], password):
            if user['role'] == 'admin':
                session['pending_user'] = username
                return redirect(url_for('mfa'))
            session['user'] = username
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        message = 'Invalid username or password.'
    return render_template('login.html', message=message)


@app.route('/register', methods=['GET', 'POST'])
def register():
    message = None
    message_type = None
    
    if request.method == 'POST':
        # Email already verified via /api/verify-email-otp
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm', '').strip()
        email = session.get('verified_email')
        
        # Validation
        if not email:
            message = 'Please verify your email first.'
            message_type = 'error'
        elif not username or not password:
            message = 'Please fill in all fields.'
            message_type = 'error'
        elif len(password) < 6:
            message = 'Password must be at least 6 characters.'
            message_type = 'error'
        elif password != confirm:
            message = 'Passwords do not match.'
            message_type = 'error'
        elif get_user(username):
            message = 'Username already exists.'
            message_type = 'error'
        else:
            # Create verified account
            password_hash = generate_password_hash(password)
            try:
                conn = sqlite3.connect('database.db')
                conn.execute(
                    'INSERT INTO users(username, email, password_hash, role, is_verified, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    (username, email, password_hash, 'user', 1, datetime.now().isoformat())
                )
                conn.commit()
                conn.close()
                
                # Send welcome email
                send_welcome_email(email, username)
                
                # Clear session
                session.pop('verified_email', None)
                session.pop('pending_email_otp', None)
                
                # Redirect to login with success message
                return redirect(url_for('login', registered='1'))
            except Exception as e:
                message = f'Registration error: {str(e)}'
                message_type = 'error'
                print(f"Registration error: {e}")
    
    verified_email = session.get('verified_email')
    return render_template('register.html', message=message, message_type=message_type, verified_email=verified_email)


@app.route('/api/send-otp-register', methods=['POST'])
def send_otp_register():
    """Send OTP to email for registration (Step 1 - Email Verification)"""
    data = request.get_json()
    email = data.get('email', '').strip()
    
    # Validate email
    if not is_valid_email(email):
        return jsonify({'success': False, 'message': 'Invalid email address'})
    
    # Check if email already registered
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE email = ?', (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': 'Email already registered'})
    
    # Generate OTP
    otp = generate_otp(6)
    otp_expiry = datetime.now() + timedelta(minutes=5)
    
    # Store OTP in session temporarily
    session['pending_email'] = email
    session['pending_email_otp'] = otp
    session['pending_otp_expiry'] = otp_expiry.isoformat()
    session['otp_attempts'] = 0
    
    conn.close()
    
    # Send OTP email
    if send_otp_email(email, otp, 'New User'):
        return jsonify({'success': True, 'message': 'OTP sent to your email', 'expiry_minutes': 5})
    else:
        # Dev mode: return OTP in response when email is not configured
        print(f"[DEV MODE] OTP for {email}: {otp}")
        return jsonify({'success': True, 'message': 'Dev Mode: Email not configured. OTP shown below.', 'dev_otp': otp, 'dev_mode': True, 'expiry_minutes': 5})


@app.route('/api/verify-email-otp', methods=['POST'])
def verify_email_otp():
    """Verify email OTP and unlock registration form (Step 2 - Email Verification Complete)"""
    data = request.get_json()
    otp_code = data.get('otp', '').strip()
    email = session.get('pending_email')
    
    # Check session
    if not email:
        return jsonify({'success': False, 'message': 'Session expired. Please try again.'})
    
    # Get stored OTP
    stored_otp = session.get('pending_email_otp')
    otp_expiry_str = session.get('pending_otp_expiry')
    otp_attempts = session.get('otp_attempts', 0)
    
    # Check expiry
    if datetime.fromisoformat(otp_expiry_str) < datetime.now():
        return jsonify({'success': False, 'message': 'OTP expired. Please request a new one.'})
    
    # Check attempts
    if otp_attempts >= 3:
        session.pop('pending_email', None)
        session.pop('pending_email_otp', None)
        return jsonify({'success': False, 'message': 'Max attempts exceeded. Please try again.'})
    
    # Verify OTP
    if otp_code == stored_otp:
        # Mark email as verified
        session['verified_email'] = email
        session.pop('pending_email_otp', None)
        session.pop('pending_otp_expiry', None)
        session.pop('otp_attempts', None)
        
        return jsonify({'success': True, 'message': 'Email verified! Fill in your credentials to register.'})
    else:
        # Increment attempts
        new_attempts = otp_attempts + 1
        session['otp_attempts'] = new_attempts
        remaining = 3 - new_attempts
        
        return jsonify({'success': False, 'message': f'Wrong OTP. {remaining} attempts remaining.'})


@app.route('/api/resend-otp-register', methods=['POST'])
def resend_otp_register():
    """Resend OTP for email verification"""
    email = session.get('pending_email')
    
    if not email:
        return jsonify({'success': False, 'message': 'Session expired. Please try again.'})
    
    # Generate new OTP
    otp = generate_otp(6)
    otp_expiry = datetime.now() + timedelta(minutes=5)
    
    # Update session
    session['pending_email_otp'] = otp
    session['pending_otp_expiry'] = otp_expiry.isoformat()
    session['otp_attempts'] = 0
    
    # Send OTP email
    if send_otp_email(email, otp, 'New User'):
        return jsonify({'success': True, 'message': 'OTP resent successfully'})
    else:
        # Dev mode: return OTP in response when email is not configured
        print(f"[DEV MODE] Resent OTP for {email}: {otp}")
        return jsonify({'success': True, 'message': 'Dev Mode: OTP shown below.', 'dev_otp': otp, 'dev_mode': True})


@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'pending_email' not in session:
        return redirect(url_for('register'))
    
    email = session.get('pending_email')
    username = session.get('pending_username')
    message = None
    message_type = None
    
    if request.method == 'POST':
        otp_code = request.form.get('otp_code', '').strip()
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT otp, otp_expiry, otp_attempts FROM users WHERE email = ?', (email,))
        row = cursor.fetchone()
        
        if not row:
            message = 'User not found.'
            message_type = 'error'
        else:
            stored_otp, otp_expiry_str, otp_attempts = row
            
            # Check OTP expiry
            if datetime.fromisoformat(otp_expiry_str) < datetime.now():
                message = 'OTP has expired. Please request a new one.'
                message_type = 'error'
            # Check attempts limit
            elif otp_attempts >= 3:
                message = 'Maximum OTP attempts exceeded. Please request a new one.'
                message_type = 'error'
                conn.execute('DELETE FROM users WHERE email = ?', (email,))
                conn.commit()
            # Verify OTP
            elif otp_code == stored_otp:
                # OTP verified - activate account
                conn.execute(
                    'UPDATE users SET is_verified = 1, otp = NULL, otp_expiry = NULL, otp_attempts = 0 WHERE email = ?',
                    (email,)
                )
                conn.commit()
                
                # Send welcome email
                send_welcome_email(email, username)
                
                # Clear session
                session.pop('pending_email', None)
                session.pop('pending_username', None)
                conn.close()
                
                return redirect(url_for('login', verified='1'))
            else:
                # Wrong OTP
                new_attempts = otp_attempts + 1
                conn.execute(
                    'UPDATE users SET otp_attempts = ? WHERE email = ?',
                    (new_attempts, email)
                )
                conn.commit()
                remaining = 3 - new_attempts
                if remaining > 0:
                    message = f'Wrong OTP. {remaining} attempts remaining.'
                else:
                    message = 'Maximum attempts exceeded.'
                message_type = 'error'
        
        conn.close()
    
    otp_expiry_time = 5  # minutes
    return render_template('verify_otp.html', email=email, message=message, message_type=message_type, otp_expiry=otp_expiry_time)


@app.route('/api/send-otp', methods=['POST'])
def send_otp_api():
    """Send OTP via AJAX"""
    data = request.get_json()
    email = data.get('email', '').strip()
    
    if not is_valid_email(email):
        return jsonify({'success': False, 'message': 'Invalid email'})
    
    # Simulate sending OTP (in production, this would be called from frontend)
    otp = generate_otp(6)
    otp_expiry = datetime.now() + timedelta(minutes=5)
    
    conn = sqlite3.connect('database.db')
    try:
        conn.execute(
            'UPDATE users SET otp = ?, otp_expiry = ?, otp_attempts = 0 WHERE email = ?',
            (otp, otp_expiry.isoformat(), email)
        )
        conn.commit()
        
        if send_otp_email(email, otp, 'User'):
            return jsonify({'success': True, 'message': 'OTP sent'})
        else:
            return jsonify({'success': False, 'message': 'Failed to send OTP'})
    except Exception as e:
        print(f"Error sending OTP: {e}")
        return jsonify({'success': False, 'message': 'Error sending OTP'})
    finally:
        conn.close()


@app.route('/api/resend-otp', methods=['POST'])
def resend_otp_api():
    """Resend OTP via AJAX"""
    email = session.get('pending_email')
    username = session.get('pending_username')
    
    if not email:
        return jsonify({'success': False, 'message': 'Session expired'})
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Generate new OTP
    otp = generate_otp(6)
    otp_expiry = datetime.now() + timedelta(minutes=5)
    
    try:
        cursor.execute(
            'UPDATE users SET otp = ?, otp_expiry = ?, otp_attempts = 0 WHERE email = ?',
            (otp, otp_expiry.isoformat(), email)
        )
        conn.commit()
        
        if send_otp_email(email, otp, username):
            return jsonify({'success': True, 'message': 'OTP resent successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to resend OTP'})
    except Exception as e:
        print(f"Error resending OTP: {e}")
        return jsonify({'success': False, 'message': 'Error resending OTP'})
    finally:
        conn.close()


@app.route('/api/verify-otp-code', methods=['POST'])
def verify_otp_code_api():
    """Verify OTP code via AJAX"""
    data = request.get_json()
    email = session.get('pending_email')
    otp_code = data.get('otp_code', '').strip()
    
    if not email:
        return jsonify({'success': False, 'message': 'Session expired'})
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT otp, otp_expiry, otp_attempts FROM users WHERE email = ?', (email,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': 'User not found'})
    
    stored_otp, otp_expiry_str, otp_attempts = row
    
    # Check OTP expiry
    if datetime.fromisoformat(otp_expiry_str) < datetime.now():
        conn.close()
        return jsonify({'success': False, 'message': 'OTP expired'})
    
    # Check attempts
    if otp_attempts >= 3:
        conn.execute('DELETE FROM users WHERE email = ?', (email,))
        conn.commit()
        conn.close()
        return jsonify({'success': False, 'message': 'Max attempts exceeded'})
    
    # Verify OTP
    if otp_code == stored_otp:
        cursor.execute('UPDATE users SET is_verified = 1, otp = NULL, otp_expiry = NULL WHERE email = ?', (email,))
        conn.commit()
        conn.close()
        
        username = session.get('pending_username')
        send_welcome_email(email, username)
        
        session.pop('pending_email', None)
        session.pop('pending_username', None)
        
        return jsonify({'success': True, 'message': 'Verified'})
    else:
        new_attempts = otp_attempts + 1
        cursor.execute('UPDATE users SET otp_attempts = ? WHERE email = ?', (new_attempts, email))
        conn.commit()
        conn.close()
        
        remaining = 3 - new_attempts
        return jsonify({'success': False, 'message': f'Wrong OTP. {remaining} attempts left'})


@app.route('/mfa', methods=['GET', 'POST'])
def mfa():
    if 'pending_user' not in session:
        return redirect(url_for('login'))
    message = None
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        if otp == '123456':
            session['user'] = session.pop('pending_user')
            session['role'] = 'admin'
            return redirect(url_for('dashboard'))
        message = 'Invalid code. Please enter 123456.'
    return render_template('mfa.html', message=message)


@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=session.get('user'), model_ready=models_loaded)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('landing'))


@app.route('/verify')
def verify():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('verify.html', model_ready=models_loaded, model_accuracy=round(model_accuracy * 100, 2) if model_accuracy is not None else None, user=session.get('user'))

@app.route('/admin')
def admin():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(CASE WHEN result = "FRAUD" THEN 1 ELSE 0 END), SUM(amount) FROM logs')
    total_tx, fraud_count, total_amount = cursor.fetchone()
    total_tx = total_tx or 0
    fraud_count = fraud_count or 0
    total_amount = total_amount or 0.0
    fraud_rate = round((fraud_count / total_tx) * 100, 2) if total_tx else 0.0
    cursor.execute('SELECT result, COUNT(*) FROM logs GROUP BY result')
    results = dict(cursor.fetchall())
    safe_count = results.get('SAFE', 0)
    suspicious_count = results.get('SUSPICIOUS', 0)
    cursor.execute('SELECT date(created_at), COUNT(*) FROM logs GROUP BY date(created_at) ORDER BY date(created_at)')
    daily_rows = cursor.fetchall()
    daily_labels = [row[0] for row in daily_rows]
    daily_counts = [row[1] for row in daily_rows]
    cursor.execute('SELECT score FROM logs')
    score_rows = cursor.fetchall()
    # Fetch all transaction logs for admin view
    cursor.execute(
        'SELECT id, username, transaction_id, upi_id, payer_name, payee_name, amount, category, result, score, device_info, geo_location, ip_address, created_at '
        'FROM logs ORDER BY created_at DESC LIMIT 200'
    )
    all_logs = [{
        'id': r[0], 'username': r[1] or 'guest', 'transaction_id': r[2] or '—',
        'upi_id': r[3] or '—', 'payer_name': r[4] or '—', 'payee_name': r[5] or '—',
        'amount': r[6], 'category': r[7] or '—', 'result': r[8] or '—',
        'score': round(r[9] * 100, 1) if r[9] is not None else 0,
        'device_info': r[10] or '—', 'geo_location': r[11] or '—',
        'ip_address': r[12] or '—', 'created_at': r[13] or '—'
    } for r in cursor.fetchall()]
    conn.close()

    risk_bins = {'Low': 0, 'Medium': 0, 'Suspicious': 0, 'High': 0}
    for (score,) in score_rows:
        if score is None:
            continue
        if score < 0.25:
            risk_bins['Low'] += 1
        elif score < 0.45:
            risk_bins['Medium'] += 1
        elif score < 0.7:
            risk_bins['Suspicious'] += 1
        else:
            risk_bins['High'] += 1

    users = get_all_users()
    return render_template(
        'admin.html',
        total_tx=total_tx,
        fraud_count=fraud_count,
        safe_count=safe_count,
        suspicious_count=suspicious_count,
        fraud_rate=fraud_rate,
        total_amount=total_amount,
        model_accuracy=round(model_accuracy * 100, 2) if model_accuracy is not None else None,
        user=session.get('user'),
        users=users,
        all_logs=all_logs,
        daily_labels=daily_labels,
        daily_counts=daily_counts,
        risk_labels=list(risk_bins.keys()),
        risk_counts=list(risk_bins.values()),
    )

@app.route('/admin/users', methods=['POST'])
def admin_users():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    action = request.form.get('action')
    if action == 'add':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'user')
        if username and password:
            create_user(username, password, role)
    elif action == 'delete':
        username = request.form.get('delete_username', '').strip()
        if username:
            delete_user(username)
    return redirect(url_for('admin'))

@app.route('/history')
def history():
    if 'user' not in session:
        return redirect(url_for('login'))
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT transaction_id, amount, category, result, score, created_at FROM logs WHERE username = ? ORDER BY created_at DESC',
        (session.get('user'),),
    )
    transactions = cursor.fetchall()
    conn.close()
    return render_template('history.html', user=session.get('user'), transactions=transactions)

@app.route('/check')
def check_page():
    return redirect(url_for('verify'))

@app.route('/api/history')
def api_history():
    if 'user' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT transaction_id, amount, category, result, score, created_at FROM logs WHERE username = ? ORDER BY created_at DESC LIMIT 50',
        (session.get('user'),),
    )
    transactions = [{'transaction_id': row[0], 'amount': row[1], 'category': row[2], 'result': row[3], 'score': row[4], 'created_at': row[5]} for row in cursor.fetchall()]
    conn.close()
    return jsonify({'transactions': transactions})


@app.route('/api/admin-stats')
def api_admin_stats():
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), SUM(CASE WHEN result = "FRAUD" THEN 1 ELSE 0 END), SUM(amount) FROM logs')
    total_tx, fraud_count, total_amount = cursor.fetchone()
    total_tx = total_tx or 0
    fraud_count = fraud_count or 0
    total_amount = total_amount or 0.0
    fraud_rate = round((fraud_count / total_tx) * 100, 2) if total_tx else 0.0
    cursor.execute('SELECT date(created_at), COUNT(*) FROM logs GROUP BY date(created_at) ORDER BY date(created_at)')
    daily_rows = cursor.fetchall()
    cursor.execute('SELECT score FROM logs')
    score_rows = cursor.fetchall()
    conn.close()
    risk_bins = {'Low': 0, 'Medium': 0, 'Suspicious': 0, 'High': 0}
    for (score,) in score_rows:
        if score is None:
            continue
        if score < 0.25:
            risk_bins['Low'] += 1
        elif score < 0.45:
            risk_bins['Medium'] += 1
        elif score < 0.7:
            risk_bins['Suspicious'] += 1
        else:
            risk_bins['High'] += 1
    return jsonify({'total_tx': total_tx, 'fraud_count': fraud_count, 'fraud_rate': fraud_rate, 'total_amount': total_amount, 'model_accuracy': round(model_accuracy * 100, 2) if model_accuracy is not None else None, 'daily_labels': [row[0] for row in daily_rows], 'daily_counts': [row[1] for row in daily_rows], 'risk_labels': list(risk_bins.keys()), 'risk_counts': list(risk_bins.values())})


@app.route('/api/users')
def api_users():
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    users = get_all_users()
    return jsonify({'users': users})


@app.route('/api/admin/all-logs')
def api_admin_all_logs():
    """Return all transaction logs for admin dashboard (live refresh)"""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, username, transaction_id, upi_id, payer_name, payee_name, amount, category, result, score, device_info, geo_location, ip_address, created_at '
        'FROM logs ORDER BY created_at DESC LIMIT 300'
    )
    rows = cursor.fetchall()
    conn.close()
    logs = [{
        'id': r[0], 'username': r[1] or 'guest', 'transaction_id': r[2] or '—',
        'upi_id': r[3] or '—', 'payer_name': r[4] or '—', 'payee_name': r[5] or '—',
        'amount': r[6], 'category': r[7] or '—', 'result': r[8] or '—',
        'score': round(r[9] * 100, 1) if r[9] is not None else 0,
        'device_info': r[10] or '—', 'geo_location': r[11] or '—',
        'ip_address': r[12] or '—', 'created_at': r[13] or '—'
    } for r in rows]
    return jsonify({'logs': logs})


@app.route('/api/admin/user-logs/<username>')
def api_user_logs(username):
    """Return transaction logs for a specific user"""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, transaction_id, upi_id, payer_name, payee_name, amount, category, result, score, device_info, geo_location, ip_address, created_at '
        'FROM logs WHERE username = ? ORDER BY created_at DESC',
        (username,)
    )
    rows = cursor.fetchall()
    conn.close()
    logs = [{
        'id': r[0], 'transaction_id': r[1] or '—', 'upi_id': r[2] or '—',
        'payer_name': r[3] or '—', 'payee_name': r[4] or '—',
        'amount': r[5], 'category': r[6] or '—', 'result': r[7] or '—',
        'score': round(r[8] * 100, 1) if r[8] is not None else 0,
        'device_info': r[9] or '—', 'geo_location': r[10] or '—',
        'ip_address': r[11] or '—', 'created_at': r[12] or '—'
    } for r in rows]
    return jsonify({'username': username, 'logs': logs})


@app.route('/api/check', methods=['POST'])
def check():
    if 'user' not in session:
        return jsonify({'error': 'Authentication required. Please log in to use fraud detection.', 'redirect': '/login'}), 401
    if not models_loaded:
        return jsonify({'error': 'ML models are not available. Please restart the app and try again.'}), 500

    json_data = request.get_json(silent=True)
    if json_data:
        data = json_data
        screenshot = None
    else:
        data = request.form.to_dict()
        screenshot = request.files.get('screenshot')

    amount = parse_amount(data.get('amount'))
    category = parse_category(data.get('category'))
    payer_name = str(data.get('payer_name', '')).strip()
    payee_name = str(data.get('payee_name', '')).strip()
    upi_id = str(data.get('upi_id', '')).strip()
    transaction_id = str(data.get('transaction_id', '')).strip()
    remarks = str(data.get('remarks', '')).strip()

    if amount is None or category is None or category < 0:
        return jsonify({'error': 'Invalid input. Enter a numeric amount and a valid category.'}), 400

    screenshot_name = None
    if screenshot and screenshot.filename:
        if allowed_file(screenshot.filename):
            filename = secure_filename(f"{int(time.time())}_{screenshot.filename}")
            screenshot_path = os.path.join(UPLOAD_DIR, filename)
            screenshot.save(screenshot_path)
            screenshot_name = filename
        else:
            return jsonify({'error': 'Screenshot file type not supported.'}), 400

    # ── V3.0: resolve context before scoring ─────────────────────────────
    ip_address  = get_client_ip()
    device_info = get_device_info()
    username    = session.get('user') if 'user' in session else None

    # ── ML ensemble prediction ─────────────────────────────────────────────
    x = np.array([[amount, category]])
    x_s = scaler.transform(x)
    dl = float(dl_model.predict(x_s, verbose=0)[0][0])
    rf_prob = float(
        rf_model.predict_proba(x_s)[0][1]
        if hasattr(rf_model, 'predict_proba')
        else rf_model.predict(x_s)[0]
    )
    base_score = 0.55 * dl + 0.45 * rf_prob

    # ── Rule-based heuristics ──────────────────────────────────────────────
    detail_score = 0.0
    if amount >= 50000:
        detail_score += 0.08
    if category in [0, 3, 6]:
        detail_score += 0.05
    # Screenshot is treated as neutral evidence — no penalty for attaching proof
    if upi_id and ('@' not in upi_id or len(upi_id) < 10):
        detail_score += 0.05
    if payer_name.lower().startswith('unknown') or payee_name.lower().startswith('unknown'):
        detail_score += 0.05

    # ── V3.0 Feature 3: NLP remarks analysis ──────────────────────────────
    nlp_raw          = get_nlp_score(remarks)
    nlp_contribution = round(nlp_raw * 0.12, 4)   # max +0.12
    detail_score    += nlp_contribution

    # ── V3.0 Feature 2: Velocity check ────────────────────────────────────
    velocity_boost = get_velocity_score(upi_id, ip_address)
    detail_score  += velocity_boost

    # ── V3.0 Feature 2: Amount deviation ──────────────────────────────────
    deviation_boost = get_amount_deviation_score(username, amount)
    detail_score   += deviation_boost

    # ── V3.0 Feature 4: Graph ring detection ──────────────────────────────
    ring_info  = get_ring_detail(upi_id) if upi_id else {
        'ring_detected': False, 'ring_score': 0.0,
        'distinct_ips': 0, 'distinct_users': 0
    }
    ring_boost    = ring_info.get('ring_score', 0.0)
    detail_score += ring_boost

    # ── Composite score & verdict ──────────────────────────────────────────
    score = round(min(1.0, base_score + detail_score), 4)

    if score > 0.65:
        result = 'FRAUD'
    elif score > 0.45:
        result = 'SUSPICIOUS'
    else:
        result = 'SAFE'

    # ── Human-readable note & detailed reasons ────────────────────────────
    reasons = []
    if amount >= 50000:
        reasons.append(f'💰 High transaction amount (₹{amount:,.2f} exceeds ₹50,000 threshold)')
    if category in [0, 3, 6]:
        cat_names = {0: 'Transfer', 3: 'Withdrawal', 6: 'P2P'}
        reasons.append(f'📂 High-risk transaction category: {cat_names.get(int(category), "Unknown")}')
    if screenshot_name:
        reasons.append('📸 Screenshot evidence attached (used for manual review only — does not affect score)')
    if upi_id and ('@' not in upi_id or len(upi_id) < 10):
        reasons.append(f'⚠️ Invalid UPI ID format: "{upi_id}"')
    if payer_name.lower().startswith('unknown') or payee_name.lower().startswith('unknown'):
        reasons.append('👤 Payer or payee name is marked as "Unknown"')
    if nlp_raw > 0.4:
        reasons.append(f'🔍 Remarks contain high-risk language patterns (NLP score: {nlp_raw*100:.1f}%)')
    elif nlp_raw > 0.2:
        reasons.append(f'🔍 Remarks contain moderately suspicious language (NLP score: {nlp_raw*100:.1f}%)')
    if velocity_boost > 0:
        reasons.append(f'⚡ High velocity: multiple recent transactions from this UPI/IP detected (+{velocity_boost*100:.1f}%)')
    if deviation_boost > 0:
        reasons.append(f'📈 Unusual amount: significantly higher than your 30-day average (+{deviation_boost*100:.1f}%)')
    if ring_info.get('ring_detected'):
        reasons.append(f"🕸️ Fraud ring detected — {ring_info['distinct_ips']} IPs & {ring_info['distinct_users']} users linked to this UPI")
    if base_score > 0.5:
        reasons.append(f'🧠 ML models flagged this transaction as high-risk (base score: {base_score*100:.1f}%)')

    if score > 0.65:
        note = 'Payment details indicate a high risk of fraud.'
    elif score > 0.45:
        note = 'Payment appears suspicious — verify carefully before proceeding.'
    else:
        note = 'Payment appears low risk.'
    if reasons:
        note += ' Reasons: ' + ' | '.join(reasons)

    # ── Geolocation (single external call kept here) ───────────────────────
    geo_location = get_geolocation(ip_address)

    # ── Persist to logs ────────────────────────────────────────────────────
    conn = sqlite3.connect('database.db')
    conn.execute(
        'INSERT INTO logs('
        'username, transaction_id, upi_id, payer_name, payee_name, '
        'remarks, screenshot, amount, category, result, score, error, '
        'device_info, geo_location, ip_address, '
        'nlp_score, velocity_score, ring_score'
        ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (username, transaction_id, upi_id, payer_name, payee_name,
         remarks, screenshot_name, amount, str(data.get('category')),
         result, score, None,
         device_info, geo_location, ip_address,
         round(nlp_raw, 4), round(velocity_boost, 4), round(ring_boost, 4)),
    )
    conn.commit()
    conn.close()

    # ── Email alert for high-risk ──────────────────────────────────────────
    if score > 0.6:
        transaction_dict = {
            'transaction_id': transaction_id,
            'amount':         amount,
            'category':       data.get('category'),
            'upi_id':         upi_id,
            'device_info':    device_info,
            'geo_location':   geo_location,
            'ip_address':     ip_address,
        }
        send_fraud_alert_email(username or 'Unknown', transaction_dict, score)

    response = {
        'result':          result,
        'score':           score,
        'note':            note,
        'reasons':         reasons,
        'model_accuracy':  round(model_accuracy * 100, 2) if model_accuracy is not None else None,
        'device_info':     device_info,
        'geo_location':    geo_location,
        # V3.0 extended signals
        'nlp_score':       round(nlp_raw * 100, 1),
        'velocity_boost':  round(velocity_boost * 100, 1),
        'deviation_boost': round(deviation_boost * 100, 1),
        'ring_detected':   ring_info.get('ring_detected', False),
        'ring_score':      round(ring_boost * 100, 1),
    }
    if screenshot_name:
        response['screenshot'] = screenshot_name
    return jsonify(response)


# ── V3.0 Feature 1: Admin label-log endpoint ──────────────────────────────
@app.route('/api/admin/label-log', methods=['POST'])
def admin_label_log():
    """Assign a confirmed label to a log entry so it feeds the retrain pipeline."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    data = request.get_json()
    log_id = data.get('log_id')
    label  = data.get('label')
    if not log_id or label not in ('CONFIRMED_FRAUD', 'CONFIRMED_SAFE', None):
        return jsonify({'error': 'Invalid payload. label must be CONFIRMED_FRAUD or CONFIRMED_SAFE'}), 400
    conn = sqlite3.connect('database.db')
    conn.execute('UPDATE logs SET confirmed_label = ? WHERE id = ?', (label, log_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'log_id': log_id, 'label': label})


# ── V3.0 Feature 1: Retrain trigger endpoint ──────────────────────────────
@app.route('/api/admin/retrain', methods=['POST'])
def admin_retrain():
    """Fire off the retraining pipeline as a background subprocess."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    script_path = os.path.join(app.root_path, 'scripts', 'retrain_models.py')
    if not os.path.exists(script_path):
        return jsonify({'error': 'retrain_models.py not found in scripts/'}), 404
    try:
        subprocess.Popen(
            [sys.executable, script_path],
            cwd=app.root_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({
            'success': True,
            'status':  'started',
            'message': 'Retraining started in background. Poll /api/admin/retrain-status for progress.',
        })
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


# ── V3.0 Feature 1: Retrain status polling endpoint ───────────────────────
@app.route('/api/admin/retrain-status', methods=['GET'])
def admin_retrain_status():
    """Return the current retraining progress from the JSON status file."""
    if 'user' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    status_path = os.path.join(app.root_path, 'model', 'retrain_status.json')
    if not os.path.exists(status_path):
        return jsonify({'status': 'idle', 'progress': 0,
                        'message': 'No retraining has been triggered yet.'})
    try:
        with open(status_path) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500


if __name__ == '__main__':
    app.run(debug=True)
