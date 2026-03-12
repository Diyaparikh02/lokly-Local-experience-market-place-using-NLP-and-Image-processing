import sys
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, flash, jsonify
from flask_bcrypt import Bcrypt
import mysql.connector
from mysql.connector import Error
from werkzeug.utils import secure_filename
import uuid
import stripe
from dotenv import load_dotenv
import math
import numpy as np
import requests as http_requests
import threading
import io as _io
from PIL import Image as PILImage
try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "lokly_secret_123")
bcrypt = Bcrypt(app)

# -------- Stripe Setup --------
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
stripe.api_key = STRIPE_SECRET_KEY

# -------- Cloudinary Setup (for persistent image uploads on Render) --------
_CLOUDINARY_CONFIGURED = False
if cloudinary and os.getenv("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    )
    _CLOUDINARY_CONFIGURED = True
    print("[OK] Cloudinary configured.")
else:
    print("[WARN] Cloudinary not configured – images will use local disk (not persistent on Render).")

# -------- Brevo SMTP Email Setup --------
import smtplib, sys as _mail_sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BREVO_SMTP_LOGIN = os.getenv("BREVO_SMTP_LOGIN", "")   # e.g. a460c6001@smtp-brevo.com
BREVO_SMTP_KEY   = os.getenv("BREVO_SMTP_KEY",   "")   # SMTP key from Brevo dashboard
BREVO_FROM_EMAIL = os.getenv("BREVO_FROM_EMAIL", "")   # your real email shown to recipients
BREVO_FROM_NAME  = "Lokly"


def _send_brevo(to_email, to_name, subject, html_body):
    """Send one email via Brevo SMTP. Runs in a background thread."""
    print(f"[MAIL] _send_brevo called: to={to_email} subject={subject}", flush=True)
    if not BREVO_SMTP_LOGIN or not BREVO_SMTP_KEY:
        print("[MAIL] ERROR: BREVO_SMTP_LOGIN or BREVO_SMTP_KEY is empty — cannot send email", flush=True)
        _mail_sys.stderr.write("[MAIL] ERROR: BREVO_SMTP_LOGIN or BREVO_SMTP_KEY not set in env\n")
        _mail_sys.stderr.flush()
        return
    try:
        print(f"[MAIL] Connecting to smtp-relay.brevo.com:587 as {BREVO_SMTP_LOGIN[:6]}...", flush=True)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"{BREVO_FROM_NAME} <{BREVO_FROM_EMAIL}>"
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(BREVO_SMTP_LOGIN, BREVO_SMTP_KEY)
            server.sendmail(BREVO_FROM_EMAIL, [to_email], msg.as_string())

        print(f"[MAIL] SUCCESS: sent to {to_email} | subject: {subject}", flush=True)
        _mail_sys.stderr.write(f"[MAIL] Sent to {to_email} | subject: {subject}\n")
        _mail_sys.stderr.flush()
    except Exception as ex:
        print(f"[MAIL] FAILED sending to {to_email}: {ex}", flush=True)
        _mail_sys.stderr.write(f"[MAIL] Exception sending to {to_email}: {ex}\n")
        _mail_sys.stderr.flush()


def send_email_async(to_email, to_name, subject, html_body):
    """Send email in a background thread so it never blocks the HTTP response."""
    t = threading.Thread(target=_send_brevo, args=(to_email, to_name, subject, html_body), daemon=False)
    t.start()


def build_user_email(username, activity_title, booking_date, amount_inr, transaction_id, host_name, host_email):
    paid_date = __import__('datetime').date.today().strftime('%d %b %Y')
    host_contact = f"{host_name} ({host_email})" if host_email else host_name
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:600px;margin:auto;padding:24px;">
      <div style="background:#f97316;padding:20px 28px;border-radius:12px 12px 0 0;">
        <h1 style="color:#fff;margin:0;font-size:1.5rem;">Booking Confirmed!</h1>
        <p style="color:#ffe4c4;margin:6px 0 0;">Thank you for booking with Lokly</p>
      </div>
      <div style="background:#fff;border:1px solid #f0f0f0;border-top:none;padding:28px;border-radius:0 0 12px 12px;">
        <p style="font-size:1rem;">Hi <strong>{username}</strong>,</p>
        <p>Your payment was successful and your slot is confirmed. Here are your booking details:</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0;">
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;width:40%;">Activity</td><td style="padding:10px 14px;">{activity_title}</td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;">Slot Date</td><td style="padding:10px 14px;">{booking_date}</td></tr>
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;">Host</td><td style="padding:10px 14px;">{host_contact}</td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;border-top:2px solid #f97316;">Amount Paid</td><td style="padding:10px 14px;border-top:2px solid #f97316;"><strong style="color:#f97316;">&#8377;{amount_inr:.2f}</strong></td></tr>
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;">Transaction ID</td><td style="padding:10px 14px;font-size:.85rem;word-break:break-all;">{transaction_id}</td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;">Payment Date</td><td style="padding:10px 14px;">{paid_date}</td></tr>
        </table>
        <p style="margin-top:24px;font-size:.85rem;color:#888;">If you have any questions, reply to this email or contact your host directly.</p>
        <p style="font-size:.85rem;color:#888;">See you on the day — the Lokly team</p>
      </div>
    </body></html>
    """


def build_host_email(host_name, username, user_email, activity_title, booking_date, amount_inr, transaction_id):
    paid_date = __import__('datetime').date.today().strftime('%d %b %Y')
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#222;max-width:600px;margin:auto;padding:24px;">
      <div style="background:#111;padding:20px 28px;border-radius:12px 12px 0 0;">
        <h1 style="color:#f97316;margin:0;font-size:1.5rem;">New Paid Booking!</h1>
        <p style="color:#aaa;margin:6px 0 0;">Someone booked your activity on Lokly</p>
      </div>
      <div style="background:#fff;border:1px solid #f0f0f0;border-top:none;padding:28px;border-radius:0 0 12px 12px;">
        <p style="font-size:1rem;">Hi <strong>{host_name}</strong>,</p>
        <p>Great news! A new booking has been paid for your activity.</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0;">
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;width:40%;">Activity</td><td style="padding:10px 14px;">{activity_title}</td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;">Guest Name</td><td style="padding:10px 14px;">{username}</td></tr>
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;">Guest Email</td><td style="padding:10px 14px;">{user_email}</td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;">Slot Date</td><td style="padding:10px 14px;">{booking_date}</td></tr>
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;border-top:2px solid #f97316;">Amount Paid</td><td style="padding:10px 14px;border-top:2px solid #f97316;"><strong style="color:#f97316;">&#8377;{amount_inr:.2f}</strong></td></tr>
          <tr><td style="padding:10px 14px;font-weight:700;">Transaction ID</td><td style="padding:10px 14px;font-size:.85rem;word-break:break-all;">{transaction_id}</td></tr>
          <tr style="background:#fff7ed;"><td style="padding:10px 14px;font-weight:700;">Payment Date</td><td style="padding:10px 14px;">{paid_date}</td></tr>
        </table>
        <p style="margin-top:24px;font-size:.85rem;color:#888;">Log in to your host dashboard to manage this booking.</p>
        <p style="font-size:.85rem;color:#888;">— The Lokly team</p>
      </div>
    </body></html>
    """

# -------- MySQL Connection (pool-based for reliability) --------
from mysql.connector import pooling as _mysql_pooling

_db_kwargs = dict(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "mywebsite"),
)
if os.getenv("DB_HOST", "localhost") != "localhost":
    _db_kwargs["ssl_disabled"] = False

# Create a connection pool — each request gets its own connection, no stale-connection bugs
_connection_pool = None
try:
    _connection_pool = _mysql_pooling.MySQLConnectionPool(
        pool_name="lokly_pool",
        pool_size=5,
        pool_reset_session=True,
        **_db_kwargs
    )
    print("[OK] MySQL connection pool created (size=5).")
except Exception as _pool_err:
    print(f"[ERROR] Could not create connection pool: {_pool_err}")

# Global db/cursor kept for legacy routes — refreshed via ensure_connection()
db = None
cursor = None

def get_conn():
    """Get a fresh connection from the pool (or direct connect as fallback).
    Caller MUST call conn.close() or use it in a with-statement."""
    if _connection_pool:
        return _connection_pool.get_connection()
    return mysql.connector.connect(**_db_kwargs)

# -------- Auto-reconnect helper (used by legacy routes) --------
def ensure_connection():
    """Give legacy routes a working global db + cursor.
    Reuses the existing connection if it's still alive — only reconnects when dead.
    This avoids a costly SSL handshake to the cloud DB on every request."""
    global db, cursor
    # Fast path: ping the existing connection — no network overhead if alive
    if db is not None:
        try:
            db.ping(reconnect=False)
            # Still alive — just refresh the cursor
            if cursor:
                try: cursor.close()
                except: pass
            cursor = db.cursor(dictionary=True)
            return
        except Exception:
            # Connection is dead — fall through to reconnect
            try: db.close()
            except: pass
            db = None
            cursor = None
    # Slow path: get a fresh connection from pool (only when needed)
    try:
        db = get_conn()
        cursor = db.cursor(dictionary=True)
    except Exception as e:
        print(f"[ERROR] ensure_connection failed: {e}")
        db = None
        cursor = None

# Bootstrap the initial global connection
try:
    ensure_connection()
    if db:
        print("[OK] MySQL Database connected successfully!")
except Exception as _e:
    print(f"[ERROR] Initial DB setup failed: {_e}")

# -------- Ensure tables exist (idempotent) --------
def ensure_tables():
        if not cursor:
            return

        # 1️⃣ USERS FIRST
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(120) NOT NULL UNIQUE,
                email VARCHAR(200) NOT NULL UNIQUE,
                password VARCHAR(200) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2️⃣ HOST_ACTIVITY SECOND
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS host_activity (
                id INT AUTO_INCREMENT PRIMARY KEY,
                host_user_id INT NOT NULL,
                name VARCHAR(120) NOT NULL,
                email VARCHAR(200) NOT NULL,
                title VARCHAR(200) NOT NULL,
                description TEXT,
                location VARCHAR(200),
                price DECIMAL(10,2),
                image_filename VARCHAR(255),
                category VARCHAR(100) DEFAULT 'Other',
                session_link VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (host_user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # 3️⃣ USER BOOKINGS
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_bookings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                activity_id INT NOT NULL,
                booking_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (activity_id) REFERENCES host_activity(id) ON DELETE CASCADE
            )
        """)

        # 4️⃣ ENROLLMENTS (optional)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS enrollments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                activity_id INT NOT NULL,
                user_name VARCHAR(120) NOT NULL,
                user_email VARCHAR(200) NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (activity_id) REFERENCES host_activity(id) ON DELETE CASCADE
            )
        """)

        # 5️⃣ PAYMENTS
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                booking_id INT NOT NULL,
                amount DECIMAL(10,2) NOT NULL,
                currency VARCHAR(10) DEFAULT 'INR',
                status VARCHAR(20) DEFAULT 'pending',
                payment_gateway_order_id VARCHAR(255),
                payment_gateway_payment_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (booking_id) REFERENCES user_bookings(id) ON DELETE CASCADE
            )
        """)

        # 6️⃣ CATEGORIES table + seed rows
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                slug VARCHAR(100) NOT NULL UNIQUE
            )
        """)
        cursor.execute("SELECT COUNT(*) as cnt FROM categories")
        if cursor.fetchone()["cnt"] == 0:
            cursor.executemany(
                "INSERT INTO categories (name, slug) VALUES (%s, %s)",
                [
                    ("Art & Culture",  "art-culture"),
                    ("Culinary Arts",  "culinary-arts"),
                    ("Dance",          "dance"),
                    ("Wellness",       "wellness"),
                    ("Adventure",      "adventure"),
                    ("Other",          "other"),
                ]
            )

        # 7️⃣ Add payment_status to user_bookings if not exists
        try:
            cursor.execute("""
                ALTER TABLE user_bookings
                ADD COLUMN payment_status VARCHAR(20) DEFAULT 'pending'
            """)
        except Exception:
            pass  # column already exists

        # 7️⃣ Add popularity tracking columns to host_activity
        try:
            cursor.execute("""
                ALTER TABLE host_activity
                ADD COLUMN total_bookings INT DEFAULT 0
            """)
        except Exception:
            pass  # column already exists

        try:
            cursor.execute("""
                ALTER TABLE host_activity
                ADD COLUMN total_clicks INT DEFAULT 0
            """)
        except Exception:
            pass  # column already exists

        db.commit()

if cursor:
    ensure_tables()

# -------- File Upload Setup --------
UPLOAD_FOLDER = os.path.join("static", "uploads")
HOST_IMG_FOLDER = os.path.join(app.root_path, "static", "images")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(HOST_IMG_FOLDER, exist_ok=True)

def _resolve_img_url(fn):
    """Return a displayable image URL from the stored image_filename value.
    Handles: Cloudinary https URLs, legacy local filenames, and None."""
    if not fn:
        return "/static/images/home.png"
    if fn.startswith("http"):
        return fn  # Cloudinary URL stored directly
    return f"/static/uploads/host_activity/{fn}"

# -------- Dummy Data (for experiences only) --------
# -------- Full Dummy Data (for experiences – expanded to match category_page) --------
experiences = [
    # Art & Culture
    {"id": 1, "title": "Pottery Workshop", "location": "Jaipur", "price": 1500,
     "image_url": "/static/images/pp5.png", "description": "Learn pottery with local artisans in Jaipur.",
     "category": "Art & Culture"},

    # Culinary Arts
    {"id": 2, "title": "Street Food Tour", "location": "Delhi", "price": 800,
     "image_url": "/static/images/pp6.png", "description": "Taste the best street food with a local guide.",
     "category": "Culinary Arts"},
    {"id": 3, "title": "Cooking Class", "location": "Mumbai", "price": 1200,
     "image_url": "/static/images/pp7.png", "description": "Cook traditional dishes with a local chef.",
     "category": "Culinary Arts"},

    # Dance
    {"id": 4, "title": "Classical Kathak Dance", "location": "Lucknow", "price": 1000,
     "image_url": "/static/images/kathak.jpg",
     "description": "Experience the grace of Kathak from professional dancers.", "category": "Dance"},
    {"id": 5, "title": "Bollywood Dance Workshop", "location": "Mumbai", "price": 900,
     "image_url": "/static/images/bollywood.jpg",
     "description": "Learn fun Bollywood moves with an energetic instructor.", "category": "Dance"},
    {"id": 6, "title": "Bharatanatyam Performance", "location": "Chennai", "price": 1100,
     "image_url": "/static/images/bharat.jpg", "description": "Immerse yourself in the ancient art of Bharatanatyam.",
     "category": "Dance"},

    # Wellness
    {"id": 7, "title": "Morning Yoga by the Beach", "location": "Goa", "price": 1300,
     "image_url": "/static/images/yoga.jpg", "description": "Rejuvenate your mind and body with yoga at sunrise.",
     "category": "Wellness"},

    # Adventure
    {"id": 8, "title": "Himalayan Trekking", "location": "Manali", "price": 2500,
     "image_url": "/static/images/himtrek.jpg", "description": "Join a guided trekking adventure in the Himalayas.",
     "category": "Adventure"},

]

bookings = []  # [{experience, date}]
saved = []     # [experience]

# -------- Helpers --------

def db_activities_as_cards():
    """Fetch host_activities and shape them like your existing 'experiences' cards."""
    if not cursor:
        return []
    cursor.execute("SELECT * FROM host_activity ORDER BY created_at DESC")
    rows = cursor.fetchall()
    mapped = []
    for r in rows:
        mapped.append({
            "id": int(10_000 + r["id"]),  # offset to avoid id clash with dummy ones
            "db_id": r["id"],             # real DB id
            "title": r["title"],
            "location": r.get("location"),
            "price": float(r["price"]) if r["price"] is not None else 0,
        "image_url": _resolve_img_url(r.get("image_filename")),
            "description": r.get("description", ""),
            "category": r.get("category", ""),
            "host_name": r.get("name", ""),
            "host_email": r.get("email", ""),
        })
    return mapped

def find_host_activity_by_db_id(db_id):
    if not cursor:
        return None
    cursor.execute("SELECT * FROM host_activity WHERE id=%s", (db_id,))
    return cursor.fetchone()

# Maps slug → hero image for category cards
CATEGORY_IMAGES = {
    "art-culture":   "/static/images/art.jpg",
    "culinary-arts": "/static/images/pp7.png",
    "dance":         "/static/images/kathak.jpg",
    "wellness":      "/static/images/yoga.jpg",
    "adventure":     "/static/images/himtrek.jpg",
    "other":         "/static/images/home.png",
}

DEFAULT_CATEGORIES = [
    {"slug": "art-culture",   "name": "Art & Culture",  "image": "/static/images/art.jpg"},
    {"slug": "culinary-arts", "name": "Culinary Arts",  "image": "/static/images/pp7.png"},
    {"slug": "dance",         "name": "Dance",          "image": "/static/images/kathak.jpg"},
    {"slug": "wellness",      "name": "Wellness",       "image": "/static/images/yoga.jpg"},
    {"slug": "adventure",     "name": "Adventure",      "image": "/static/images/himtrek.jpg"},
]

def get_categories_from_db():
    """Read categories from the DB categories table."""
    try:
        ensure_connection()
        c = db.cursor(dictionary=True)
        c.execute("SELECT name, slug FROM categories ORDER BY id")
        rows = c.fetchall()
        if not rows:
            return DEFAULT_CATEGORIES
        return [
            {"slug": r["slug"], "name": r["name"],
             "image": CATEGORY_IMAGES.get(r["slug"], "/static/images/home.png")}
            for r in rows
        ]
    except Exception:
        return DEFAULT_CATEGORIES


# ── Single source of truth for all static/dummy activities ──────────────────
STATIC_ACTIVITIES = [
    {"id": 1, "title": "Pottery Workshop",         "location": "Jaipur",  "price": 1500,
     "image_url": "/static/images/pp5.png",       "description": "Learn pottery with local artisans in Jaipur.",              "category": "Art & Culture"},
    {"id": 2, "title": "Street Food Tour",          "location": "Delhi",   "price": 800,
     "image_url": "/static/images/pp6.png",       "description": "Taste the best street food with a local guide.",            "category": "Culinary Arts"},
    {"id": 3, "title": "Cooking Class",             "location": "Mumbai",  "price": 1200,
     "image_url": "/static/images/pp7.png",       "description": "Cook traditional dishes with a local chef.",                "category": "Culinary Arts"},
    {"id": 4, "title": "Classical Kathak Dance",    "location": "Lucknow", "price": 1000,
     "image_url": "/static/images/kathak.jpg",    "description": "Experience the grace of Kathak from professional dancers.", "category": "Dance"},
    {"id": 5, "title": "Bollywood Dance Workshop",  "location": "Mumbai",  "price": 900,
     "image_url": "/static/images/bollywood.jpg", "description": "Learn fun Bollywood moves with an energetic instructor.",   "category": "Dance"},
    {"id": 6, "title": "Bharatanatyam Performance", "location": "Chennai", "price": 1100,
     "image_url": "/static/images/bharat.jpg",    "description": "Immerse yourself in the ancient art of Bharatanatyam.",    "category": "Dance"},
    {"id": 7, "title": "Morning Yoga by the Beach", "location": "Goa",     "price": 1300,
     "image_url": "/static/images/yoga.jpg",      "description": "Rejuvenate your mind and body with yoga at sunrise.",       "category": "Wellness"},
    {"id": 8, "title": "Himalayan Trekking",        "location": "Manali",  "price": 2500,
     "image_url": "/static/images/himtrek.jpg",   "description": "Join a guided trekking adventure in the Himalayas.",        "category": "Adventure"},
]

# IDs 1-3 are shown in the Featured section on the home page
FEATURED_IDS = {1, 2, 3}


# ── NLP Semantic Search Setup ─────────────────────────────────────────────────
_nlp_model = None

def _get_nlp_model():
    global _nlp_model
    if _nlp_model is None:
        print("Loading NLP model (all-MiniLM-L6-v2)...")
        from sentence_transformers import SentenceTransformer
        _nlp_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _nlp_model

# ── Per-category concept tags ──────────────────────────────────────────────
CATEGORY_TAGS = {
    "art & culture":  "creative artistic crafts pottery clay painting heritage culture handmade traditions sculpting artwork visual art",
    "culinary arts":  "food cooking baking cake pie pastry dessert cuisine kitchen recipes chef eating taste flavors ingredients street food snacks",
    "dance":          "energetic fun performance rhythm movement choreography artistic expression stage entertainment fitness",
    "wellness":       "peaceful relaxation mindfulness stress relief calm yoga meditation health serene tranquil balance inner peace breathe sunrise",
    "adventure":      "mountains outdoor hiking trekking nature physical activity thrill explore expedition wilderness trails fitness",
    "other":          "local activity experience workshop class session hands-on skill learn community hosted unique creative fun",
}

# ── Keyword Inverted Index ─────────────────────────────────────────────────
# Maps search terms → {static_activity_id: weight}
# This is the PRIMARY scorer. Cosine similarity is only used as fallback
# when no keyword index hit exists (e.g. hosted activities or unknown words).
# Static IDs: 1=Pottery, 2=StreetFood, 3=Cooking, 4=Kathak,
#             5=Bollywood, 6=Bharatanatyam, 7=Yoga, 8=Trekking
KEYWORD_INDEX = {
    # ── Pottery Workshop (1) ──────────────────────────────────────────────
    "pottery":      {1: 10},
    "clay":         {1: 9},
    "ceramic":      {1: 9},
    "ceramics":     {1: 9},
    "wheel":        {1: 8},
    "sculpt":       {1: 7},
    "sculpting":    {1: 7},
    "sculpture":    {1: 7},
    "molding":      {1: 6},
    "mold":         {1: 6},
    "artisan":      {1: 7},
    "artisans":     {1: 7},
    "craft":        {1: 6},
    "crafts":       {1: 6},
    "handmade":     {1: 6},
    "jaipur":       {1: 10},
    "kiln":         {1: 8},
    "glaze":        {1: 7},
    "glazing":      {1: 7},
    "terracotta":   {1: 8},
    "throw":        {1: 7},
    "throwing":     {1: 7},
    "artwork":      {1: 6},
    "painting":     {1: 5},
    "creative":     {1: 6},
    "hands-on":     {1: 6},
    "handson":      {1: 6},
    "art class":    {1: 7},
    "art workshop": {1: 8},
    "craft class":  {1: 7},
    "diy":          {1: 6},
    # ── Street Food Tour (2) ──────────────────────────────────────────────
    "street":       {2: 10},
    "snack":        {2: 9},
    "snacks":       {2: 9},
    "chaat":        {2: 9},
    "vendor":       {2: 7},
    "vendors":      {2: 7},
    "tour":         {2: 8},
    "tasting":      {2: 7, 3: 4},
    "taste":        {2: 7, 3: 4},
    "flavors":      {2: 7, 3: 4},
    "flavor":       {2: 7, 3: 4},
    "market":       {2: 7},
    "delhi":        {2: 10},
    "spicy":        {2: 7},
    "eating":       {2: 7, 3: 5},
    "eat":          {2: 7, 3: 5},
    "foodie":       {2: 10},
    "foodies":      {2: 10},
    "guide":        {2: 8},
    "tasty":        {2: 8},
    "delicious":    {2: 8},
    "yummy":        {2: 7},
    "sample":       {2: 7},
    "sampling":     {2: 7},
    "try":          {2: 6},
    "trying":       {2: 6},
    "eats":         {2: 9},
    "bite":         {2: 7},
    "bites":        {2: 7},
    "stall":        {2: 8},
    "stalls":       {2: 8},
    "roadside":     {2: 9},
    "local food":   {2: 9},
    "best food":    {2: 8},
    "hawker":       {2: 8},
    "indian food":  {2: 8},
    # ── Cooking Class (3) ─────────────────────────────────────────────────
    "cook":         {3: 10, 2: 3},
    "cooking":      {3: 10, 2: 3},
    "dessert":      {3: 4},
    "recipe":       {3: 8},
    "recipes":      {3: 8},
    "kitchen":      {3: 8},
    "chef":         {3: 9},
    "dishes":       {3: 7},
    "cuisine":      {3: 7, 2: 5},
    "ingredients":  {3: 7},
    "preparation":  {3: 6},
    "learn":        {3: 4, 5: 3},
    "mumbai":       {3: 10},
    "meal":         {3: 7},
    "meals":        {3: 7},
    "homemade":     {3: 8},
    "spices":       {3: 7},
    "spice":        {3: 7},
    "masala":       {3: 8},
    "curry":        {3: 8},
    "dal":          {3: 7},
    "roti":         {3: 7},
    "biryani":      {3: 7},
    "culinary class":{3: 9},
    "food class":   {3: 8},
    "cook class":   {3: 9},
    "learn to cook":{3: 9},
    "cooking lesson":{3: 9},
    "cooking workshop":{3: 8},
    "dish":         {3: 7},
    "homestyle":    {3: 7},
    "authentic":    {3: 6},
    "flavour":      {3: 6, 2: 5},
    # ── Classical Kathak Dance (4) ────────────────────────────────────────
    "kathak":       {4: 10},
    "classical":    {4: 8, 6: 7},
    "lucknow":      {4: 10},
    "grace":        {4: 7},
    "elegant":      {4: 7},
    "recital":      {4: 7},
    "graceful":     {4: 7},
    "footwork":     {4: 8},
    "ghungroo":     {4: 9},
    "tabla":        {4: 8},
    "nawab":        {4: 7},
    "north indian":  {4: 7},
    "court dance":  {4: 8},
    "classical indian": {4: 8, 6: 7},
    "indian classical": {4: 8, 6: 7},
    "dance class":  {4: 7, 5: 7, 6: 6},
    "dance lesson": {4: 7, 5: 7, 6: 6},
    "dance workshop":{4: 6, 5: 8, 6: 6},
    "learn dance":  {4: 6, 5: 7},
    "traditional dance": {4: 8, 6: 7},
    # ── Bollywood Dance Workshop (5) ──────────────────────────────────────
    "bollywood":    {5: 10},
    "hindi":        {5: 7},
    "film":         {5: 6},
    "filmy":        {5: 6},
    "moves":        {5: 7},
    "fun":          {5: 6},
    "energetic":    {5: 7, 8: 6},
    "instructor":   {5: 6},
    "steps":        {5: 7},
    "songs":        {5: 7},
    "music":        {5: 6},
    "item":         {5: 6},
    "popular":      {5: 5},
    "beginner":     {5: 6},
    "beginners":    {5: 6},
    "easy":         {5: 5},
    "movie dance":  {5: 8},
    "filmy dance":  {5: 8},
    "dance steps":  {5: 8},
    "hindi songs":  {5: 8},
    "item song":    {5: 7},
    "famous":       {5: 5},
    "party":        {5: 6},
    "lively":       {5: 6},
    "upbeat":       {5: 6},
    # ── Bharatanatyam Performance (6) ─────────────────────────────────────
    "bharatanatyam":{6: 10},
    "chennai":      {6: 10},
    "temple":       {6: 7},
    "devotional":   {6: 7},
    "ancient":      {6: 6},
    "spiritual":    {6: 7, 7: 5},
    "south":        {6: 7},
    "immerse":      {6: 6},
    "tamil":        {6: 8},
    "mudra":        {6: 9},
    "mudras":       {6: 9},
    "abhinaya":     {6: 9},
    "nritya":       {6: 8},
    "divine":       {6: 7},
    "religious":    {6: 6},
    "south indian": {6: 8},
    "classical art": {6: 7},
    "performance art": {6: 7},
    "show":         {6: 6},
    "watch":        {6: 5},
    "witness":      {6: 6},
    "classical performance": {6: 8},
    "indian dance": {6: 7, 4: 6},
    # ── Morning Yoga by the Beach (7) ─────────────────────────────────────
    "yoga":         {7: 10},
    "meditation":   {7: 9},
    "peaceful":     {7: 10},
    "peace":        {7: 9},
    "calm":         {7: 9},
    "tranquil":     {7: 9},
    "serene":       {7: 9},
    "relax":        {7: 9},
    "relaxing":     {7: 9},
    "relaxation":   {7: 9},
    "mindfulness":  {7: 9},
    "mindful":      {7: 8},
    "sunrise":      {7: 8},
    "beach":        {7: 9},
    "goa":          {7: 10},
    "wellness":     {7: 9},
    "health":       {7: 7},
    "breath":       {7: 7},
    "breathe":      {7: 7},
    "morning":      {7: 6},
    "rejuvenate":   {7: 7},
    "mind":         {7: 6},
    "balance":      {7: 7},
    "stress":       {7: 7},
    "body":         {7: 5},
    "inner":        {7: 6},
    "yoga class":   {7: 9},
    "yoga session": {7: 9},
    "yoga retreat": {7: 9},
    "beach yoga":   {7: 9},
    "morning yoga": {7: 10},
    "outdoor yoga": {7: 8},
    "asana":        {7: 8},
    "asanas":       {7: 8},
    "pranayama":    {7: 8},
    "stretching":   {7: 7},
    "stretch":      {7: 6},
    "flexibility":  {7: 7},
    "flexible":     {7: 6},
    "holistic":     {7: 7},
    "soothing":     {7: 8},
    "anxiety":      {7: 7},
    "heal":         {7: 6},
    "healing":      {7: 7},
    "sun salutation":{7: 9},
    "exercise":     {7: 5},
    "workout":      {7: 4},
    "fit":          {7: 4},
    "wellbeing":    {7: 8},
    "well-being":   {7: 8},
    "zen":          {7: 8},
    # ── Himalayan Trekking (8) ────────────────────────────────────────────
    "trek":         {8: 10},
    "trekking":     {8: 10},
    "hike":         {8: 10},
    "hiking":       {8: 10},
    "himalayan":    {8: 10},
    "himalaya":     {8: 10},
    "himalayas":    {8: 10},
    "manali":       {8: 10},
    "mountains":    {8: 10},
    "mountain":     {8: 9},
    "outdoor":      {8: 9},
    "adventure":    {8: 9},
    "wilderness":   {8: 8},
    "trails":       {8: 8},
    "trail":        {8: 8},
    "expedition":   {8: 8},
    "snow":         {8: 8},
    "nature":       {8: 7},
    "physical":     {8: 5},
    "fitness":      {8: 5, 7: 4},
    "explore":      {8: 6},
    "guided":       {8: 5, 2: 5},
    "camping":      {8: 8},
    "camp":         {8: 7},
    "backpacking":  {8: 8},
    "backpack":     {8: 7},
    "summit":       {8: 9},
    "peak":         {8: 8},
    "altitude":     {8: 8},
    "climb":        {8: 8},
    "climbing":     {8: 8},
    "thrill":       {8: 7},
    "thrilling":    {8: 7},
    "glacier":      {8: 8},
    "scenic":       {8: 6},
    "landscape":    {8: 6},
    "landscapes":   {8: 6},
    "hills":        {8: 7},
    "hill":         {8: 6},
    "rohtang":      {8: 9},
    "pine":         {8: 5},
    "forest":       {8: 5},
    "stamina":      {8: 6},
    "challenging":  {8: 7},
    "challenge":    {8: 6},
    "rugged":       {8: 6},
    "outdoor adventure": {8: 9},
    "mountain trek": {8: 10},
    "hiking adventure": {8: 9},
    # Extra camping/outdoor night vocabulary
    "bonfire":      {8: 9},
    "campfire":     {8: 9},
    "fire":         {8: 6},
    "stargazing":   {8: 8},
    "night sky":    {8: 8},
    "night trek":   {8: 9},
    "campsite":     {8: 8},
    "marshmallow":  {8: 7},
    "wildlife":     {8: 7},
    "terrain":      {8: 7},
    "valley":       {8: 7},
    "slopes":       {8: 7},
    "offroad":      {8: 7},
    "off-road":     {8: 7},
    "rough":        {8: 6},
    # ── Low-energy / relaxed activity queries → Yoga (7) ──────────────────
    "low energy":       {7: 10},
    "low-energy":       {7: 10},
    "low intensity":    {7: 10},
    "low-intensity":    {7: 10},
    "gentle activity":  {7: 10},
    "easy activity":    {7: 10},
    "relaxed activity": {7: 10},
    "relaxing activity":{7: 10},
    "calm activity":    {7: 10},
    "soft activity":    {7: 9},
    "chill activity":   {7: 9},
    "lazy activity":    {7: 9},
    "slow activity":    {7: 9},
    "gentle activities":{7: 10},
    "easy activities":  {7: 10},
    "relaxed activities":{7: 10},
    "relaxing activities":{7:10},
    "calm activities":  {7: 10},
    "chill activities": {7: 9},
    "slow activities":  {7: 9},
    "lazy activities":  {7: 9},
    "low energy activities": {7: 10},
    "low-energy activities": {7: 10},
    "non-strenuous":    {7: 9},
    "non strenuous":    {7: 9},
    "undemanding":      {7: 8},
    "lighthearted":     {7: 7},
    "mellow":           {7: 9},
    "laid back":        {7: 9},
    "laid-back":        {7: 9},
    "easygoing":        {7: 8},
    "easy-going":       {7: 8},
    "chill":            {7: 9},
    "lazy":             {7: 8},
    "gentle":           {7: 9},
    "soft":             {7: 7},
    # ── High-energy / active activity queries → Bollywood (5) + Trekking (8) ──
    "high energy":             {5: 10, 8: 9},
    "high-energy":             {5: 10, 8: 9},
    "high energy activities":  {5: 10, 8: 9},
    "high-energy activities":  {5: 10, 8: 9},
    "energetic activities":    {5: 10, 8: 8},
    "energetic activity":      {5: 10, 8: 8},
    "active activities":       {5: 8,  8: 9},
    "active activity":         {5: 8,  8: 9},
    "high intensity":          {5: 8,  8: 9},
    "high-intensity":          {5: 8,  8: 9},
    "intense activities":      {8: 10, 5: 8},
    "intense activity":        {8: 10, 5: 8},
    "adrenaline":              {8: 10},
    "adrenaline rush":         {8: 10},
    "heart pumping":           {8: 9,  5: 8},
    "heart-pumping":           {8: 9,  5: 8},
    "strenuous":               {8: 9},
    "physical activity":       {8: 8,  5: 6},
    "physically active":       {8: 9,  5: 7},
    "get active":              {8: 8,  5: 7},
    "get moving":              {5: 8,  8: 7},
    "sweat":                   {5: 7,  8: 6},
    "action packed":           {8: 8,  5: 7},
    "action-packed":           {8: 8,  5: 7},
    "exciting activities":     {8: 8,  5: 7},
    "exciting activity":       {8: 8,  5: 7},
    "thrilling activity":      {8: 9},
    "thrilling activities":    {8: 9},
    "fun activities":          {5: 8,  8: 6},
    "fun activity":            {5: 8},
    "energy":                  {5: 6,  8: 5},
    # ── Cross-category ────────────────────────────────────────────────────
    "dance":        {4: 7, 5: 7, 6: 7},
    "dancing":      {4: 7, 5: 7, 6: 7},
    "performance":  {4: 6, 6: 8},
    "workshop":     {1: 5, 3: 5, 5: 5},
    "cultural":     {1: 6, 4: 5, 6: 5},
    "culture":      {1: 7},
    "art":          {1: 7, 6: 4},
    "artistic":     {1: 6, 4: 4, 6: 5},
    "heritage":     {1: 7},
    "traditional":  {3: 5, 4: 6, 6: 6},
    "food":         {2: 9, 3: 6},
    "local":        {1: 3, 2: 5, 3: 4},
    "indian":       {3: 4, 4: 6, 6: 7},
    "india":        {1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2},
    "movement":     {4: 5, 5: 5, 6: 5},
    "choreography": {4: 5, 5: 6, 6: 5},
    "rhythm":       {4: 5, 5: 5, 6: 5},
}


# ── Baking query keywords that should always surface hosted "Baking" activities ──
BAKING_TRIGGER_WORDS = {
    "bake", "baking", "cake", "pie", "pastry", "bread", "dough", "oven",
    "muffin", "brownie", "croissant", "cookies", "tart", "sourdough", "knead",
    "homebaked", "bakery", "confection", "patisserie", "biscuit",
    "pie making", "cake making", "bread making", "pastry making",
    "baking class", "baking session", "baking lesson", "baking workshop",
    "make cake", "make pie", "bake cake", "bake bread",
    "home baked", "freshly baked",
}


def _keyword_score(act, query_words):
    """Deterministic score from the inverted keyword index + direct text match."""
    act_id = act.get("id", -1)
    title  = act["title"].lower()
    desc   = act.get("description", "").lower()
    cat    = act.get("category", "").lower()
    loc    = act.get("location", "").lower()
    score  = 0
    for word in query_words:
        # Primary: inverted index hit (highest confidence)
        index_hit = KEYWORD_INDEX.get(word, {})
        score += index_hit.get(act_id, 0)
        # Secondary: direct text substring match (catches hosted activities)
        if word in title:    score += 4
        if word in loc:      score += 3
        if word in cat:      score += 2
        if word in desc:     score += 1
    # Also check bigrams (two consecutive words as a phrase)
    for i in range(len(query_words) - 1):
        bigram = query_words[i] + " " + query_words[i + 1]
        index_hit = KEYWORD_INDEX.get(bigram, {})
        score += index_hit.get(act_id, 0)
    return score


# ── Per-activity synonym tags (for semantic embedding enrichment) ──────────
ACTIVITY_TAGS = {
    # ─── Pottery Workshop ──────────────────────────────────────────────────
    # Covers: tactile/creative descriptions, therapy angle, Rajasthani culture
    "pottery workshop": (
        "clay wheel sculpting ceramic handmade craft jaipur art creative artisan molding shaping "
        "terracotta kiln glaze firing earthenware vessel bowl vase pot mug moulding spinning "
        "diy crafts hands-on tactile muddy messy therapeutic stress-relief focus patience slow "
        "traditional rajasthani heritage culture artsy artistic studio workshop creative class "
        "pigment paint decorating design pattern texture form sculpture three-dimensional "
        "calming mindful meditative slow-paced handcraft homemade unique souvenir keepsake "
        "blue pottery decorative artwork installation craft beer glass throwing beginners beginners "
        "indian heritage local artisan community cultural experience jaipur rajasthan"
    ),
    # ─── Street Food Tour ──────────────────────────────────────────────────
    # Covers: sensory/taste descriptions, atmosphere, culinary tourism
    "street food tour": (
        "street food snacks guide local flavors eating tasting tour delhi chaat vendor market spicy "
        "crispy crunchy fried grilled sizzling smoky aroma fragrant mouth-watering delicious tasty "
        "yummy lip-smacking cheap affordable budget hawker stall cart roadside lane alley bazaar "
        "pani puri bhel puri tikki samosa jalebi chole bhature golgappa roll kathi kebab "
        "foodie food lover gourmet bite nibble munch snacking grazing eating out dining "
        "vibrant colourful lively buzzing crowded noisy chaotic bustling busy night market "
        "culinary journey gastronomic adventure food walk tasting session flavour exploration "
        "spice heat tang sweet sour savoury umami bold intense rich authentic rustic "
        "old delhi chandni chowk connaught place karol bagh street culture urban "
        "local knowledge hidden gem secret spot off-the-beaten-path undiscovered authentic "
        "communal sharing plate try new flavors adventurous palate food experience"
    ),
    # ─── Cooking Class ─────────────────────────────────────────────────────
    # Covers: home cooking, recipes, savoury indian cuisine
    "cooking class": (
        "cooking class recipes cook kitchen chef traditional dishes mumbai cuisine learn preparation "
        "roast fry saute simmer boil steam stir-fry toss sear flambe deglaze reduce caramelise "
        "masala curry dal roti naan chapati biryani pulao sabzi paneer spice blend tadka tempering "
        "recipe technique skill homemade fresh ingredients wholesome nutritious healthy "
        "indian cooking desi food regional cuisines maharashtrian south indian north indian "
        "hands-on practical demo demonstration step-by-step guided instruction tutorial "
        "chopping dicing slicing prep kitchen tools knife cutting board wok tawa kadai "
        "flavourful aromatic richly-spiced colourful plating presentation garnish "
        "family style communal sharing meal together dinner lunch brunch "
        "learn a skill impress friends take home cookbook culinary arts food education "
        "chef secrets professional tips food science cooking chemistry"
    ),
    # ─── Classical Kathak Dance ────────────────────────────────────────────
    # Covers: classical music/dance vocabulary, cultural heritage, north india
    "classical kathak dance": (
        "kathak classical indian dance lucknow grace performance traditional recital elegant "
        "ghungroo ankle bells footwork tatkaar chakkars spin turn twirl rhythmic rhythm "
        "tabla pakhawaj taal laya bandish thumri dadra composition raga "
        "mudra hasta hand gesture expression abhinaya emotion storytelling "
        "north indian mughal nawabi court dance heritage cultural "
        "lehenga choli costume makeup stage spotlight performance recital "
        "guru shishya teacher student discipline rigorous training practice "
        "classical art form intangible heritage preservation ancient tradition "
        "lyrical fluid graceful poised dignified regal aristocratic "
        "dance drama narrative mythology devotion bhakti "
        "varanasi jaipur lucknow gharana school lineage spiritual devotional"
    ),
    # ─── Bollywood Dance Workshop ──────────────────────────────────────────
    # Covers: fun/party/energetic angle, pop culture, beginner-friendly
    "bollywood dance workshop": (
        "bollywood dance workshop film songs moves energetic fun instructor mumbai hindi music steps "
        "item song peppy peppy beat groovy upbeat lively party celebration festive "
        "choreography routine sequence learn copy mimic follow along "
        "shah rukh kajol priyanka hrithik zumba aerobic cardio fitness dance workout "
        "beginner easy simple no-experience casual social fun group activity "
        "wedding sangeet reception party preparation corporate team-building icebreaker "
        "popular song latest trending film movie soundtrack "
        "colourful costume saree dupatta outfit dressing up "
        "group dance ensemble sync coordination teamwork laughter joy happy "
        "high-energy sweat burn calories active movement jumping clapping "
        "selfie reel instagram video social media trendy viral"
    ),
    # ─── Bharatanatyam Performance ─────────────────────────────────────────
    # Covers: classical south indian, devotional, ancient art
    "bharatanatyam performance": (
        "bharatanatyam classical south indian dance performance chennai temple art devotional ancient spiritual "
        "mudra hasta hand gesture abhinaya facial expression navarasas emotions "
        "adavu basic steps nritta nritya natya pure dance expressive narrative "
        "tamilnadu carnatic music mridangam veena flute nattuvanar "
        "silk saree jewellery anklets temple jewellery makeup stage lighting "
        "devadasi tradition ancient temple dance bharata muni natyashastra "
        "spiritual devotion deity god goddess shiva nataraja cosmic dance "
        "elaborate intricate precise disciplined classical rigorous "
        "arangetram debut recital concert stage performance live "
        "south india culture heritage preservation documentation "
        "divine grace beauty aesthetics rasa emotion viewer experience "
        "slow meditative graceful expressive storytelling mythological"
    ),
    # ─── Morning Yoga by the Beach ─────────────────────────────────────────
    # Covers: wellness/mental health words, sensory/mood descriptors
    "morning yoga by the beach": (
        "yoga beach peaceful calm morning sunrise relaxation meditation wellness goa mindfulness tranquil serene breathe "
        "tranquility serenity inner peace harmony stillness quiet silence solitude "
        "asana pranayama breath control breathing exercise inhale exhale "
        "sun salutation surya namaskar downward dog warrior tree pose lotus savasana "
        "stretch flexibility balance core strength posture alignment body awareness "
        "ocean wave sand barefoot nature outdoors fresh air cool breeze salty "
        "rejuvenate recharge refresh revitalise energise awaken body mind spirit "
        "de-stress destress anxiety relief tension release mental health emotional balance "
        "holistic healing spiritual growth self-care self-love wellbeing "
        "zen buddhist mindful slow gentle restorative yin vinyasa hatha "
        "goa coastal paradise tropical warm humid beautiful scenic "
        "sunrise dawn early morning golden hour peaceful before the world wakes "
        "stress-free worry-free let go surrender accept present moment awareness "
        "calm mind clear head focused grounded centred rooted "
        "guided instructor certified yoga teacher beginner friendly all levels"
    ),
    # ─── Himalayan Trekking ────────────────────────────────────────────────
    # Covers: outdoor adventure, camping culture, nature experiences
    "himalayan trekking": (
        "trekking hiking himalayan mountains manali snow outdoor trails adventure nature wilderness expedition "
        "bonfire campfire fire warmth roasting marshmallow smores gathering circle "
        "stargazing night sky milky way constellations clear sky altitude dark sky "
        "camping camp tent sleeping bag bivouac overnight stay under stars "
        "summit peak altitude high altitude acclimatise thin air cold freezing "
        "glacier snowfield moraine scree rocky rugged terrain off-trail "
        "rohtang pass solang valley beas river forest pine cedar rhododendron "
        "wildlife marmot snow leopard eagle hawk bird watching fauna flora "
        "suspension bridge rope bridge river crossing stream waterfall "
        "valley meadow alpine meadow pasture grassland shepherd nomad "
        "backpack rucksack trekking pole boot sock layering jacket raincoat "
        "challenging strenuous physical stamina endurance fitness test "
        "sunrise summit golden hour panoramic view 360 degree landscape "
        "silent remote isolated peaceful away from city detox digital detox "
        "guide porter local guide team crew support safety first aid "
        "slow travel sustainable eco-friendly leave no trace wilderness ethics "
        "chilly cold freezing subzero hot chocolate soup warming campfire food "
        "group bonding teamwork shared hardship togetherness camaraderie "
        "rewarding achievement accomplishment proud summit certificate "
        "raw unfiltered natural pristine untouched pure clean "
        "sky-high above-the-clouds bird's-eye view incredible views photogenic "
        "thrill adrenaline rush excitement goosebumps heart-pounding breathtaking"
    ),
}

def _activity_text(e):
    """Richly enriched text for embedding — category tags + per-activity synonym tags."""
    title       = e['title']
    description = e.get('description', '')
    category    = e.get('category', '')
    location    = e.get('location', '')
    cat_tags    = CATEGORY_TAGS.get(category.strip().lower(), "")
    act_tags    = ACTIVITY_TAGS.get(title.strip().lower(), "")
    text = f"Title: {title}. Description: {description}. Category: {category}. Location: {location}."
    if cat_tags:
        text += f" Category concepts: {cat_tags}."
    if act_tags:
        text += f" Activity concepts: {act_tags}."
    return text.lower()

# Lazy-load static NLP embeddings on first search
_static_texts      = [_activity_text(e) for e in STATIC_ACTIVITIES]
_static_embeddings = None

def _get_static_embeddings():
    global _static_embeddings
    if _static_embeddings is None:
        _static_embeddings = _get_nlp_model().encode(
            _static_texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        print(f"NLP ready. {len(STATIC_ACTIVITIES)} static embeddings precomputed.")
    return _static_embeddings


# ── CLIP Image-Search Setup ────────────────────────────────────────────────
# Uses OpenAI CLIP (via sentence-transformers) with multi-prompt ensembling.
# Each activity has several visually concrete prompts; we take the MAX score
# across all prompts — this dramatically improves per-activity accuracy.

# Curated visual prompts keyed by activity title (lowercase).
# Each prompt describes what you would literally SEE in a matching photo.
_CLIP_VISUAL_PROMPTS = {
    "pottery workshop": [
        "a person shaping wet clay on a spinning pottery wheel",
        "hands pressing and moulding soft clay on a wheel",
        "artisan crafting handmade ceramic bowls in a pottery studio",
        "close-up of fingers smoothing the rim of a clay pot",
        "unfired clay vessels drying on a wooden shelf",
        "pottery workshop with clay-stained hands and a kick wheel",
        "freshly thrown clay bowl still wet on the wheel",
        "potter trimming the base of an earthen pot",
        "colourful glazed ceramic pots lined up in a kiln room",
        "wet clay being centred on a spinning pottery wheel",
        "terracotta pots being shaped by hand in a kiln workshop",
        "3D clay sculpture being smoothed with pottery tools",
    ],
    "drawing and sketching": [
        "person sketching a portrait with pencil on white paper",
        "artist drawing detailed lines in a sketchbook",
        "hand holding pencil drawing on a white canvas or sheet",
        "open sketchbook with pencil drawings and shading",
        "art student doing still-life pencil sketching in a class",
        "close-up of pencil strokes on paper creating a sketch",
        "charcoal sketch of a face on white drawing paper",
        "drawing class with students and easels holding paper",
        "artist outlining a figure with graphite pencil",
        "hands doing cross-hatching shading on a pencil drawing",
        "pencil illustration being drawn in a notebook",
        "fine art drawing workshop with pencils and paper sheets",
    ],
    "painting workshop": [
        "person painting on canvas with a brush and acrylic paints",
        "artist applying brushstrokes of colour on a canvas",
        "watercolour painting session with brushes and a palette",
        "painter mixing colours on a palette at an art class",
        "colourful painting of a landscape or portrait in progress",
        "acrylic paint tubes and brushes on a painter's table",
        "art class with students painting at easels",
        "hands holding a paintbrush creating strokes on canvas",
        "oil painting workshop with vibrant pigments on canvas",
        "mandala or folk art painting being created with fine brushes",
        "painting display of completed works at an art workshop",
        "student artist mixing watercolours in a tray",
    ],
    "street food tour": [
        "street food stalls lit up at night with vendors and crowds",
        "colourful street market food with people eating and tasting",
        "local street snacks served on banana leaf or paper plate",
        "steaming hot street food being cooked on a large iron pan",
        "busy outdoor food bazaar with spicy dishes and condiments",
        "tourists and guide sampling chaat papri pani-puri at a stall",
        "vendor deep-frying samosas at a roadside cart",
        "rows of street food carts with bright signboards and customers",
        "close-up of colourful Indian street food on a plate",
        "food tour group walking through a busy market lane",
        "spice stalls with red yellow and green powders in baskets",
        "person eating pani puri at a crowded street stall",
    ],
    "cooking class": [
        "people cooking together in a kitchen class with an instructor",
        "hands chopping fresh vegetables on a wooden cutting board",
        "cooking teacher demonstrating how to make curry",
        "participants learning to roll chapati dough in a class",
        "home kitchen with cast-iron pots and traditional spices",
        "cooking lessons with mixing bowls, masalas, and utensils",
        "students tasting food they cooked during a culinary workshop",
        "wok on a gas stove with vegetables being stir-fried",
        "chef showing knife skills to students in an apron",
        "traditional Indian kitchen with tandoor and clay pots",
        "dessert making class with flour sugar and baking tools",
        "meal preparation class with colourful ingredients on a table",
    ],
    "classical kathak dance": [
        "Indian classical Kathak dancer spinning on stage in costume",
        "dancer wearing ghungroo ankle bells and lehenga choli",
        "Kathak dance recital with expressive mudra hand gestures",
        "classical Indian dance performance with elaborate jewellery",
        "female Kathak dancer mid-spin with flared skirt",
        "Kathak performer with kohl eyes and red bindi on forehead",
        "Indian classical dance stage lighting dramatic pose",
        "Kathak feet close-up with ghungroo bells tied above ankle",
        "Indian dancer in heavy embroidered costume performing tatkaar",
        "Kathak dance show in an auditorium with audience",
        "dance guru demonstrating footwork to students in practice room",
        "classical Indian dance student in practice with mirror",
    ],
    "bollywood dance workshop": [
        "group of smiling people dancing Bollywood moves in a studio",
        "colourful Bollywood dance rehearsal class with instructor",
        "fun group dance workshop with bright costumes and energy",
        "participants learning Bollywood choreography in a gym hall",
        "energetic dance class with arms raised and people laughing",
        "Bollywood item number style dance performance on stage",
        "people in colourful Indian outfits dancing in a circle",
        "choreographer teaching Bollywood steps to a group",
        "dance floor with people learning Filmi dance moves",
        "Bollywood fusion dance routine in a studio mirror setting",
        "ladies in salwar kameez doing Bollywood garba steps",
        "youth group practising Bollywood music video choreography",
    ],
    "bharatanatyam performance": [
        "Bharatanatyam dancer in full stage costume with oil lamp",
        "classical Bharatanatyam posture with araimandi and mudras",
        "South Indian classical dancer with temple jewellery and silk saree",
        "dancer in Bharatanatyam makeup with large eye extensions",
        "Bharatanatyam performance on temple stage with traditional lamps",
        "classical Indian dance with elaborate hair flowers and bangles",
        "Bharatanatyam soloist performing alarippu with expressive eyes",
        "dancer stamping feet rhythmically in Bharatanatyam nattadavu",
        "colourful Bharatanatyam costume with pleated fan saree",
        "traditional South Indian dance arangetram debut performance",
        "Bharatanatyam student practising adavu in a classical dance school",
        "classical dance mudra close-up with decorated fingers and henna",
    ],
    "morning yoga by the beach": [
        "person doing yoga pose on the beach at sunrise",
        "yoga mat on sand with ocean waves in the background",
        "woman in warrior pose on a beach at dawn",
        "group outdoor yoga class by the sea at sunrise",
        "meditation sitting cross-legged on sand with ocean view",
        "sunrise yoga session with silhouette against the sea",
        "surya namaskar sun salutation on beach in morning light",
        "yoga instructor leading class on a tropical beach",
        "person stretching in downward dog on outdoor yoga mat",
        "peaceful beach yoga with trees and calm water at sunrise",
        "woman in tree pose balancing on one leg on sandy beach",
        "breathing exercises pranayama sitting on beach at dawn",
    ],
    "himalayan trekking": [
        "hikers with backpacks trekking on a Himalayan mountain trail",
        "group climbing a rocky high-altitude mountain path",
        "trekking through pine forests towards snow-capped Himalayan peaks",
        "mountain trail in the Himalayas with dramatic misty valleys",
        "trekkers crossing a suspension bridge over a Himalayan river",
        "hikers setting up camp at a mountain base with snow peaks",
        "walking on a narrow ridge trail in the Himalayas",
        "mountaineers with trekking poles on a steep rocky slope",
        "panoramic view of snow-capped peaks during a high-altitude trek",
        "group of trekkers resting at a mountain tea house",
        "sunrise over the Himalayas seen from a campsite",
        "solo trekker silhouette against vast mountain landscape",
        # Campfire / bonfire — extremely common at trekking campsites
        "campers sitting around a bonfire in the mountains at night",
        "bonfire at a mountain campsite with tents and pine trees",
        "outdoor campfire with trekkers gathered around it in the dark",
        "glowing campfire in a forest clearing during a camping trip",
        "people warming hands around a fire at a high-altitude camp",
        "night sky with stars and a campfire burning at a mountain camp",
        "burning bonfire at a wilderness camp with people sitting around",
        "trekking camp at night with orange flame of campfire",
    ],
}

# ── Category-level visual prompt banks for DB-hosted activities ───────────────
# These describe what you'd literally SEE in a photo from each category.
# Used as the base layer for any hosted activity whose title doesn't keyword-
# match a curated static entry.
_CLIP_CATEGORY_VISUAL_PROMPTS = {
    "art & culture": [
        "artist creating handmade crafts in a traditional workshop",
        "people painting or drawing in an art class",
        "craftsman carving or sculpting with traditional tools",
        "colourful handmade artwork displayed in a cultural studio",
        "block printing or textile dyeing workshop with fabric and ink",
        "heritage craft class with artisans and handmade objects",
        "pottery and ceramics being made in a clay workshop",
        "cultural performance or heritage demonstration on stage",
    ],
    "culinary arts": [
        "people cooking together in a kitchen workshop",
        "hands chopping vegetables and preparing fresh ingredients",
        "chef teaching students to cook a recipe",
        "steaming pots of food and spices on a kitchen stove",
        "colourful street food being served at outdoor stalls",
        "baking bread pastry or dessert in a bakery class",
        "spice market with colourful powders and condiments",
        "tasting session with small plates of food on a table",
        "cooking class participants gathered around a kitchen counter",
    ],
    "dance": [
        "people dancing energetically in a dance studio",
        "dance workshop with instructor demonstrating moves",
        "performers on stage in colourful dance costumes",
        "group of students learning choreography in a mirror-lined studio",
        "classical Indian dancer with traditional costume and jewellery",
        "dance recital with expressive hand gestures and footwork",
        "fitness dance class with upbeat group exercise",
        "folk dance performance with traditional outfits",
    ],
    "wellness": [
        "person doing yoga in a peaceful outdoor setting",
        "meditation session with people sitting cross-legged",
        "group yoga class on a beach or park at sunrise",
        "wellness retreat with calm natural scenery",
        "breathing exercises or pranayama in a serene environment",
        "spa or healing therapy session with candles and calm ambience",
        "people stretching or doing morning exercise outdoors",
        "mindfulness class in a quiet indoor studio",
    ],
    "adventure": [
        "hikers with backpacks trekking on a mountain trail",
        "group rock climbing on a rocky cliff face",
        "outdoor adventure activity in a forest or river",
        "kayaking or rafting on a fast-moving river",
        "camping tent set up in the wilderness under stars",
        "paragliding or zip-lining in a mountainous landscape",
        "people mountain biking on a forest trail",
        "adventure group rappelling down a waterfall",
        "scenic mountain landscape with trekkers and snow peaks",
        "bonfire at an outdoor campsite with people sitting around it",
        "glowing campfire in the wilderness at night with tents nearby",
        "campers gathered around a fire at a forest campsite",
        "burning woodfire at a mountain camping spot under stars",
    ],
    "other": [
        "people participating in a hands-on workshop",
        "group activity session with participants and an instructor",
        "community gathering for a skill-building class",
        "outdoor event or local cultural experience",
        "people learning a new skill in a classroom setting",
    ],
}

# Keyword → static activity title mapping for title-based prompt injection.
# If a hosted activity title contains any of these keywords, we inject those
# curated static prompts as well — dramatically boosting accuracy for activities
# that share a concept with known static entries.
_CLIP_TITLE_KEYWORD_MAP = {
    "yoga":           "morning yoga by the beach",
    "meditat":        "morning yoga by the beach",
    "trek":           "himalayan trekking",
    "hik":            "himalayan trekking",
    "mountain":       "himalayan trekking",
    "climb":          "himalayan trekking",
    "campfire":       "himalayan trekking",
    "bonfire":        "himalayan trekking",
    "camping":        "himalayan trekking",
    "pottery":        "pottery workshop",
    "clay":           "pottery workshop",
    "ceramic":        "pottery workshop",
    "cook":           "cooking class",
    "cuisine":        "cooking class",
    "bak":            "cooking class",
    "food tour":      "street food tour",
    "street food":    "street food tour",
    "food walk":      "street food tour",
    "kathak":         "classical kathak dance",
    "bollywood":      "bollywood dance workshop",
    "bharatanatyam":  "bharatanatyam performance",
    "sketch":         "drawing and sketching",
    "draw":           "drawing and sketching",
    "illustrat":      "drawing and sketching",
    "pencil art":     "drawing and sketching",
    "charcoal":       "drawing and sketching",
    "paint":          "painting workshop",
    "canvas":         "painting workshop",
    "watercolour":    "painting workshop",
    "watercolor":     "painting workshop",
    "acrylic":        "painting workshop",
    "dance":          "bollywood dance workshop",
    "danc":           "bollywood dance workshop",
}

def _clip_fallback_prompts(e):
    """
    Generate rich visual prompts for DB-hosted activities.

    Strategy (3 layers, giving ~15+ prompts):
      1. Title-keyword injection: if the title overlaps with a known static
         activity, pull those curated 12-prompt embeddings in.
      2. Title+description based visual sentences.
      3. Category-level visual scene prompts.
    """
    title    = e.get("title",       "").strip()
    desc     = e.get("description", "").strip()
    category = e.get("category",    "").strip().lower()
    location = e.get("location",    "").strip()
    title_l  = title.lower()

    prompts = []

    # Layer 1 — inject curated static prompts if title keyword matches
    for kw, static_key in _CLIP_TITLE_KEYWORD_MAP.items():
        if kw in title_l and static_key in _CLIP_VISUAL_PROMPTS:
            prompts.extend(_CLIP_VISUAL_PROMPTS[static_key])
            break  # one match is enough; avoid duplicating similar sets

    # Layer 2 — title + description visual sentences
    if title and desc:
        prompts += [
            f"a photo showing {title.lower()}",
            f"people doing {title.lower()} in {location}" if location else f"people doing {title.lower()}",
            f"an image of {desc.lower()[:120]}",
            f"{title.lower()} activity with participants",
        ]
    elif title:
        prompts += [
            f"a photo showing {title.lower()}",
            f"people doing {title.lower()}",
        ]

    # Layer 3 — category visual scene prompts (always included)
    cat_key = category
    # Normalise alternate spellings
    if "art" in cat_key or "cultur" in cat_key:
        cat_key = "art & culture"
    elif "culinary" in cat_key or "food" in cat_key:
        cat_key = "culinary arts"
    elif "danc" in cat_key:
        cat_key = "dance"
    elif "well" in cat_key or "yoga" in cat_key or "health" in cat_key:
        cat_key = "wellness"
    elif "advent" in cat_key or "sport" in cat_key or "outdoor" in cat_key:
        cat_key = "adventure"
    else:
        cat_key = "other"
    prompts.extend(_CLIP_CATEGORY_VISUAL_PROMPTS.get(cat_key, _CLIP_CATEGORY_VISUAL_PROMPTS["other"]))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in prompts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique

def _get_clip_prompts(e):
    """Return the list of visual prompts for a given activity dict."""
    key = e.get("title", "").strip().lower()
    return _CLIP_VISUAL_PROMPTS.get(key) or _clip_fallback_prompts(e)

_clip_model = None

def _get_clip_model():
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer
        print("Loading CLIP model (clip-ViT-B-32) for image-based search...")
        _clip_model = SentenceTransformer('clip-ViT-B-32')
    return _clip_model

def _encode_prompts(prompts):
    """Encode a list of text prompts and return normalised embeddings."""
    embs = _get_clip_model().encode(
        prompts, convert_to_numpy=True,
        normalize_embeddings=True, show_progress_bar=False,
    )  # shape (n_prompts, 512)
    return embs  # already unit-normalised

# Lazy-load CLIP static embeddings on first image search
_clip_static_prompt_embs = None

def _get_clip_static_embs():
    global _clip_static_prompt_embs
    if _clip_static_prompt_embs is None:
        _clip_static_prompt_embs = [
            _encode_prompts(_get_clip_prompts(e))
            for e in STATIC_ACTIVITIES
        ]
        print(f"CLIP ready. {len(STATIC_ACTIVITIES)} static activities with multi-prompt embeddings.")
    return _clip_static_prompt_embs


def get_hosted_activities():
    """Fetch all user-hosted activities from DB and normalise into dicts."""
    try:
        c = db.cursor(dictionary=True)
        c.execute("SELECT * FROM host_activity ORDER BY id DESC")
        rows = c.fetchall()
        result = []
        for r in rows:
            result.append({
                "id": int(10_000 + r["id"]),
                "db_id": r["id"],
                "title": r["title"],
                "location": r.get("location") or "",
                "price": float(r["price"]) if r["price"] else 0,
            "image_url": _resolve_img_url(r.get("image_filename")),
            "description": r.get("description") or "",
            "category": r.get("category") or "Other",
            "total_bookings": int(r.get("total_bookings") or 0),
            "total_clicks":   int(r.get("total_clicks")   or 0),
            })
        return result
    except Exception:
        return []


def get_all_activities():
    """Return ALL activities (static + DB-hosted) as one flat list."""
    return STATIC_ACTIVITIES + get_hosted_activities()


def get_user_preferred_categories(user_id):
    """Return the set of categories the user has previously booked."""
    if not user_id or not db:
        return set()
    try:
        c = db.cursor(dictionary=True)
        c.execute("""
            SELECT DISTINCT ha.category
            FROM user_bookings ub
            JOIN host_activity ha ON ha.id = ub.activity_id
            WHERE ub.user_id = %s AND ha.category IS NOT NULL
        """, (user_id,))
        rows = c.fetchall()
        return {r["category"] for r in rows if r["category"]}
    except Exception:
        return set()


def search_experiences(query=None, city=None):
    """
    Always-on hybrid search: keyword score + semantic score blended together.

    Pipeline
    --------
    1. Build pool: static + DB-hosted activities.
    2. Auto-detect city from query if not supplied.
    3. No query → return pool as-is.
    4. Compute keyword score (deterministic index + direct text match).
    5. Compute semantic cosine similarity via NLP model.
    6. Normalise keyword score to 0-1, then blend:
         final = keyword_norm * 0.65 + semantic * 0.35
       → When a keyword hits, it dominates (accurate for known words).
       → When keyword = 0 for all activities, semantic is the sole ranker
         (handles unknown / creative / typo-adjacent words automatically).
    7. Return top results with score > threshold, never empty.
    """
    hosted   = get_hosted_activities()
    all_acts = list(STATIC_ACTIVITIES) + hosted

    if not city and query:
        city = extract_city_from_query(query, all_acts)

    if city:
        city_lower = city.strip().lower()
        pool = [a for a in all_acts if city_lower in a.get("location", "").lower()]
    else:
        pool = all_acts

    if not query:
        return pool

    ql          = query.strip().lower()
    query_words = ql.split()

    # ── Step 1: Keyword scores ────────────────────────────────────────────
    kw_scores = [_keyword_score(act, query_words) for act in pool]
    max_kw    = max(kw_scores) if kw_scores else 0
    # Normalise to 0-1 (avoid div-by-zero)
    kw_norm = [s / max_kw if max_kw > 0 else 0.0 for s in kw_scores]

    # ── Step 2: Semantic scores ───────────────────────────────────────────
    try:
        if len(pool) == len(all_acts):
            if hosted:
                hosted_embs = _get_nlp_model().encode(
                    [_activity_text(e) for e in hosted],
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                pool_embeddings = np.vstack([_get_static_embeddings(), hosted_embs])
            else:
                pool_embeddings = _get_static_embeddings()
        else:
            pool_embeddings = _get_nlp_model().encode(
                [_activity_text(e) for e in pool],
                convert_to_numpy=True,
                normalize_embeddings=True,
            )

        q_emb    = _get_nlp_model().encode([ql], convert_to_numpy=True, normalize_embeddings=True)
        sem_scores = (q_emb @ pool_embeddings.T).flatten().tolist()

    except Exception as e:
        print(f"[search_experiences] semantic error: {e}")
        # If model fails, fall back to keyword-only
        sem_scores = [0.0] * len(pool)

    # ── Step 3: Blend and rank ────────────────────────────────────────────
    blended = []
    for kw_n, sem, act in zip(kw_norm, sem_scores, pool):
        final = kw_n * 0.75 + sem * 0.25
        blended.append((final, act))

    blended.sort(key=lambda x: x[0], reverse=True)

    if max_kw > 0:
        # Keyword hit: return all activities that scored above zero threshold
        MIN_SCORE = 0.22
        result = [act for score, act in blended if score >= MIN_SCORE]
        if not result:
            result = [blended[0][1]]   # safety: at least the top hit
    else:
        # Purely unknown word — use raw semantic scores directly
        # (blended would just be 0.35×sem which is always tiny; compare sem raw)
        sem_ranked = sorted(zip(sem_scores, pool), key=lambda x: x[0], reverse=True)
        top_sem    = sem_ranked[0][0]   # best cosine score

        # Adaptive threshold:
        #   very confident (top > 0.45) → return only the best match
        #   moderately confident (>0.30) → return top 2 if runner-up is close
        #   low confidence → return top 1 (best guess)
        if top_sem >= 0.45:
            result = [sem_ranked[0][1]]
        elif top_sem >= 0.30:
            # Include runner-up only if it's within 0.05 of the best
            result = [act for s, act in sem_ranked if s >= top_sem - 0.05][:2]
        else:
            result = [sem_ranked[0][1]]  # best guess even if confidence is low

    # ── Hosted activity text-match injection ──────────────────────────────
    # Hosted activities can't be in KEYWORD_INDEX (dynamic IDs) so when static
    # keyword scores dominate, hosted activities get normalised near zero and
    # fall below MIN_SCORE even though they're directly relevant.
    # Fix: for every hosted activity NOT yet in result, compute a simple text-
    # match score across title / location / category / description.
    # Any positive hit means the activity is relevant → include it.
    # Build all query unigrams + bigrams for phrase matching.
    all_query_tokens = set(query_words)
    for i in range(len(query_words) - 1):
        all_query_tokens.add(query_words[i] + " " + query_words[i + 1])

    hosted_hits = []  # (text_match_score, act)
    for act in hosted:
        if act in result:
            continue
        t = act["title"].lower()
        l = act.get("location", "").lower()
        c = act.get("category", "").lower()
        d = act.get("description", "").lower()
        txt_score = 0
        for tok in all_query_tokens:
            if tok in t: txt_score += 4
            if tok in l: txt_score += 3
            if tok in c: txt_score += 2
            if tok in d: txt_score += 1
        # Also check baking triggers for the hosted Baking activity
        if all_query_tokens & BAKING_TRIGGER_WORDS and "baking" in t:
            txt_score += 10
        if txt_score > 0:
            hosted_hits.append((txt_score, act))

    # Sort by match strength and prepend the strongest hosted hit(s)
    hosted_hits.sort(key=lambda x: x[0], reverse=True)
    for _, act in hosted_hits:
        result.insert(0, act)

    return result[:5]


# search_activities kept as alias so home() and global_search() need no changes
def search_activities(q):
    return search_experiences(query=q or None)


def extract_city_from_query(query, activities):
    """
    Scan the query string for any city that appears in the activities pool.
    Returns the matched city string (lowercase) or None.
    """
    known_cities = {
        act["location"].strip().lower()
        for act in activities
        if act.get("location")
    }
    query_lower = query.lower()
    for city in known_cities:
        if city in query_lower:
            return city
    return None


# ── Slug → category name map ─────────────────────────────────────────────────
SLUG_TO_CATEGORY = {
    "art-culture":   "Art & Culture",
    "culinary-arts": "Culinary Arts",
    "dance":         "Dance",
    "wellness":      "Wellness",
    "adventure":     "Adventure",
}


# -------- Routes --------
@app.route("/", methods=["GET"])
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))

    q = request.args.get("q", "").strip()

    # Featured section: only the 3 curated static + all DB-hosted
    hosted = get_hosted_activities()
    featured = [e for e in STATIC_ACTIVITIES if e["id"] in FEATURED_IDS] + hosted

    # Search uses ALL activities (no featured dependency)
    search_results = search_activities(q) if q else []

    categories = get_categories_from_db()

    return render_template("home.html",
                           experiences=featured,
                           search_results=search_results,
                           categories=categories,
                           q=q)

@app.route("/category/<slug>")
def category_page(slug):
    category_name = SLUG_TO_CATEGORY.get(slug, slug.replace("-", " ").title())

    # Filter from unified pool (static + hosted)
    all_acts = get_all_activities()
    filtered = [e for e in all_acts if e["category"].lower() == category_name.lower()]

    return render_template("category.html", experiences=filtered, category=category_name)


@app.route("/about")
def about():
    files = os.listdir(UPLOAD_FOLDER)
    blogs = []
    for f in files:
        if f.endswith(".txt"):
            path = os.path.join(UPLOAD_FOLDER, f)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    content = file.read()
            except Exception:
                content = ""
            blogs.append({"title": f.replace(".txt", ""), "content": content})
    reels = [f for f in files if f.endswith((".mp4", ".mov", ".avi", ".mkv"))]
    return render_template("about.html", blogs=blogs, reels=reels)

@app.route("/upload_experience", methods=["POST"])
def upload_experience():
    title = request.form.get("title")
    exp_type = request.form.get("type")
    content = request.form.get("content")
    file = request.files.get("media")
    if exp_type == "blog" and content and title:
        safe_title = "".join(c for c in title if c.isalnum() or c in (" ","-","")).strip().replace(" ", "")
        with open(os.path.join(UPLOAD_FOLDER, f"{safe_title}.txt"), "w", encoding="utf-8") as f:
            f.write(content)
        flash("✅ Blog uploaded successfully!", "success")
    elif exp_type == "reel" and file and file.filename:
        filename = secure_filename(file.filename)
        unique = f"{uuid.uuid4().hex[:8]}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, unique)
        file.save(filepath)
        flash("✅ Reel uploaded successfully!", "success")
    else:
        flash("⚠ Please provide valid content.", "danger")
    return redirect(url_for("about"))

@app.route("/host", methods=["GET", "POST"])
def host():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        details = request.form.get("details")
        print(f"[HOST APPLICATION] {name} | {email}\n{details}\n")
        return redirect(url_for("home"))
    return render_template("host.html")


# ---------- Global Search ----------
@app.route("/search")
def global_search():
    if "user_id" not in session:
        return redirect(url_for("login"))

    q = request.args.get("q", "").strip()
    results = search_activities(q)
    all_categories = [c["name"] for c in get_categories_from_db()]

    return render_template("search_results.html",
                           results=results,
                           q=q,
                           all_categories=all_categories)


# ---------- Click Tracking ----------
@app.route("/track/click/<int:activity_id>", methods=["POST"])
def track_click(activity_id):
    """Increment total_clicks for a hosted activity (DB id without the 10000 offset)."""
    if not db:
        return jsonify({"status": "error", "msg": "no db"}), 500
    try:
        # activity_id may arrive as the offset id (10000+) or the raw db id
        db_id = activity_id - 10_000 if activity_id > 10_000 else activity_id
        c = db.cursor()
        c.execute("""
            UPDATE host_activity
            SET total_clicks = total_clicks + 1
            WHERE id = %s
        """, (db_id,))
        db.commit()
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 500


# ---------- All Experiences (search + city + category filter) ----------
@app.route("/experiences")
def all_experiences():
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Accept both ?search= (NLP query) and ?city= (location filter)
    search            = request.args.get("search",   "").strip()
    city              = request.args.get("city",     "").strip()
    selected_category = request.args.get("category", "").strip()

    # Run the unified search pipeline
    results = search_experiences(
        query = search or None,
        city  = city   or None,
    )

    # Optional extra filter: category chips in the UI
    if selected_category:
        results = [e for e in results
                   if e["category"].lower() == selected_category.lower()]

    all_categories = [c["name"] for c in get_categories_from_db()]

    return render_template("experiences.html",
                           experiences=results,
                           all_categories=all_categories,
                           selected_category=selected_category,
                           search=search,
                           city=city)


# ---------- Become a Host ----------
@app.route("/become-host", methods=["POST"])
def become_host():
    if "user_id" not in session:
        flash("Please login to become a host.", "warning")
        return redirect(url_for("login"))

    name = request.form.get("name", "").strip()
    title = request.form.get("activity", "").strip()
    description = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    price = request.form.get("price", "").strip()
    category = request.form.get("category", "Other").strip()
    session_link = request.form.get("session_link", "").strip()

    host_user_id = session["user_id"]

    # ---------- IMAGE UPLOAD ----------
    image = request.files.get("image")
    image_filename = None

    if image and image.filename:
        unique_name = uuid.uuid4().hex[:8]
        filename = secure_filename(image.filename)
        if _CLOUDINARY_CONFIGURED:
            try:
                upload_result = cloudinary.uploader.upload(
                    image,
                    public_id=f"lokly/host_activity/{unique_name}",
                    overwrite=True,
                    resource_type="image"
                )
                image_filename = upload_result["secure_url"]
            except Exception as ce:
                print(f"[ERROR] Cloudinary upload failed: {ce}")
                # Fall back to local disk on Cloudinary failure
                image_filename = f"{unique_name}_{filename}"
                image.seek(0)
                image.save(os.path.join(HOST_IMG_FOLDER, image_filename))
        else:
            image_filename = f"{unique_name}_{filename}"
            image.save(os.path.join(HOST_IMG_FOLDER, image_filename))

    # ---------- INSERT INTO DATABASE ----------
    try:
        ensure_connection()

        # Get host email from users table
        cursor.execute("SELECT email FROM users WHERE id=%s", (host_user_id,))
        user_row = cursor.fetchone()
        host_email = user_row["email"] if user_row else ""

        cursor.execute("""
            INSERT INTO host_activity
            (host_user_id, name, email, title, description, location, price, image_filename, category, session_link)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            host_user_id,
            name,
            host_email,
            title,
            description,
            location,
            price,
            image_filename,
            category,
            session_link
        ))

        db.commit()
    except Exception as e:
        print(f"[ERROR] become_host DB insert failed: {e}")
        flash(f"Error saving activity: {e}", "danger")
        return redirect(url_for("host"))

    session["is_host"] = True

    flash("✅ Activity added! You can manage it from your Host Dashboard.", "success")
    return redirect(url_for("host_dashboard"))

# ---------- Experience Detail ----------
@app.route("/experience_detail/<int:id>")
def experience_detail(id):
    if id < 10_000:
        exp = next((e for e in STATIC_ACTIVITIES if e["id"] == id), None)
        if not exp:
            return "Experience not found", 404
        exp = dict(exp)          # copy so we don't mutate the global list
        exp["is_hosted"] = False
        return render_template("experience_detail.html", experience=exp)
    db_id = id - 10_000
    rec = find_host_activity_by_db_id(db_id)
    if not rec:
        print(f"[ERROR] Hosted experience not found for DB id {db_id} (full id {id})")  # Debug log
        return "Experience not found", 404
    exp = {
        "id": id,
        "title": rec["title"],
        "location": rec.get("location"),
        "price": float(rec["price"]) if rec["price"] is not None else 0,
        "image_url": _resolve_img_url(rec.get("image_filename")),
        "description": rec.get("description", ""),
        "host_name": rec.get("name"),
        "session_link": rec.get("session_link"),
        "category": rec.get("category", "Other"),
        "is_hosted": True  # Flag for template (hosted = enroll button)
    }
    print(f"[OK] Loading hosted experience: {exp['title']} (id {id}, DB {db_id})")  # Debug log
    return render_template("experience_detail.html", experience=exp)

# ---------- Enrollment ----------
@app.route("/enroll/<int:id>", methods=["GET", "POST"])
def enroll(id):
    if id < 10_000:
        flash("Enrollment is only for community-hosted activities.", "warning")
        return redirect(url_for("home"))

    db_id = id - 10_000
    rec = find_host_activity_by_db_id(db_id)
    if not rec:
        return "Experience not found", 404
    if request.method == "POST":
        user_name = request.form.get("user_name", "").strip()
        user_email = request.form.get("user_email", "").strip()
        note = request.form.get("note", "").strip()
        cursor.execute(
            "INSERT INTO enrollments (activity_id, user_name, user_email, note) VALUES (%s, %s, %s, %s)",
            (db_id, user_name, user_email, note)
        )
        db.commit()
        flash("🎉 Enrolled successfully! The host will share the teaching session link.", "success")
        return redirect(url_for("home"))
    return render_template_string("""
    <div style="max-width:560px;margin:40px auto;font-family:system-ui;">
      <h2>Enroll: {{ title }}</h2>
      <form method="POST">
        <label>Name</label>
        <input name="user_name" required style="width:100%;padding:10px;margin:6px 0;">
        <label>Email</label>
        <input type="email" name="user_email" required style="width:100%;padding:10px;margin:6px 0;">
        <label>Note (optional)</label>
        <textarea name="note" style="width:100%;padding:10px;margin:6px 0;"></textarea>
        <button type="submit" style="padding:10px 14px;border-radius:8px;background:#f59e0b;border:0;font-weight:700;">Enroll</button>
      </form>
      <p style="margin-top:12px;"><a href="{{ url_for('home') }}">← Back to Home</a></p>
    </div>
    """, title=rec["title"])

# ---------- Host Management ----------
@app.route("/host/manage/<int:db_id>", methods=["GET", "POST"])
def host_manage(db_id):
    rec = find_host_activity_by_db_id(db_id)
    if not rec:
        return "Activity not found", 404
    if request.method == "POST":
        session_link = request.form.get("session_link", "").strip()
        cursor.execute(
            "UPDATE host_activity SET session_link=%s WHERE id=%s",
            (session_link, db_id)
        )
        db.commit()
        flash("✅ Session link updated.", "success")
        return redirect(url_for("host_manage", db_id=db_id))
    cursor.execute("SELECT * FROM enrollments WHERE activity_id=%s ORDER BY created_at DESC", (db_id,))
    enrs = cursor.fetchall()
    return render_template_string("""
    <div style="max-width:800px;margin:40px auto;font-family:system-ui;">
      <h2>Manage Activity: {{ rec.title }}</h2>
      <p><strong>Host:</strong> {{ rec.name }} ({{ rec.email }})</p>
      <p><strong>Session Link:</strong>
        {% if rec.session_link %}<a href="{{ rec.session_link }}" target="_blank">{{ rec.session_link }}</a>{% else %}<em>not set</em>{% endif %}
      </p>
      <form method="POST" style="margin:16px 0;">
        <label>Update teaching/meeting link (Zoom/Meet/etc.)</label>
        <input name="session_link" value="{{ rec.session_link or '' }}" placeholder="https://..." style="width:100%;padding:10px;margin:6px 0;">
        <button type="submit" style="padding:10px 14px;border-radius:8px;background:#f59e0b;border:0;font-weight:700;">Save Link</button>
      </form>
      <h3>Enrolled Users ({{ enrs|length }})</h3>
      {% if enrs %}
        <ul style="padding:0;list-style:none;border:1px solid #e5e7eb;border-radius:8px;">
          {% for e in enrs %}
            <li style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">
              <strong>{{ e.user_name }}</strong> ({{ e.user_email }})
              {% if e.note %}<div style="color:#6b7280;">Note: {{ e.note }}</div>{% endif %}
              <div style="font-size:12px;color:#6b7280;">{{ e.created_at }}</div>
            </li>
          {% endfor %}
        </ul>
      {% else %}
        <p>No enrollments yet.</p>
      {% endif %}
      <p style="margin-top:12px;"><a href="{{ url_for('home') }}">← Back to Home</a></p>
    </div>
    """, rec=rec, enrs=enrs)

# ---------- Dashboard / Booking ----------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    user_id = session["user_id"]

    cursor.execute("""
        SELECT b.id, b.booking_date, b.payment_status, h.title, h.location, h.price
        FROM user_bookings b
        JOIN host_activity h ON b.activity_id = h.id
        WHERE b.user_id = %s
        ORDER BY b.created_at DESC
    """, (user_id,))

    bookings = cursor.fetchall()

    return render_template(
        "dashboard.html",
        bookings=bookings,
        username=session["username"]
    )

@app.route("/checkout/<int:id>", methods=["GET"])
def checkout(id):
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    date = request.args.get("date", "").strip()
    if not date:
        flash("Please select a date first.", "warning")
        return redirect(url_for("experience_detail", id=id))

    if id >= 10_000:
        activity_id = id - 10_000
        rec = find_host_activity_by_db_id(activity_id)
        if not rec:
            return "Experience not found", 404
        experience = {
            "id": id,
            "title": rec["title"],
            "price": float(rec["price"]) if rec["price"] else 0,
            "location": rec.get("location", ""),
            "image_url": _resolve_img_url(rec.get("image_filename")),
        }
    else:
        flash("Only hosted activities support online payment.", "danger")
        return redirect(url_for("home"))

    return render_template("checkout.html", experience=experience, date=date,
                           stripe_publishable_key=str(STRIPE_PUBLISHABLE_KEY or ""))

# ---------- Create Stripe PaymentIntent ----------
@app.route("/create-payment-intent", methods=["POST"])
def create_payment_intent():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    activity_id_offset = data.get("activity_id")
    date = data.get("date", "").strip()

    if not activity_id_offset or not date:
        return jsonify({"error": "Missing activity_id or date"}), 400

    if activity_id_offset >= 10_000:
        db_activity_id = activity_id_offset - 10_000
    else:
        return jsonify({"error": "Invalid activity"}), 400

    rec = find_host_activity_by_db_id(db_activity_id)
    if not rec:
        return jsonify({"error": "Activity not found"}), 404

    amount_inr = float(rec["price"]) if rec["price"] else 0
    amount_paise = int(amount_inr * 100)  # Stripe uses smallest currency unit

    user_id = session["user_id"]

    # Always ensure fresh connection before payment DB ops
    try:
        ensure_connection()
        cur = db.cursor(dictionary=True)
    except Exception as e:
        return jsonify({"error": f"Database connection error: {e}"}), 500

    # Create booking with payment_status = pending
    try:
        cur.execute(
            "INSERT INTO user_bookings (user_id, activity_id, booking_date, payment_status) VALUES (%s, %s, %s, 'pending')",
            (user_id, db_activity_id, date)
        )
        db.commit()
        booking_id = cur.lastrowid
    except Exception as e:
        cur.close()
        return jsonify({"error": f"DB error creating booking: {e}"}), 500

    # Create Stripe PaymentIntent
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_paise,
            currency="inr",
            metadata={"booking_id": booking_id, "user_id": user_id}
        )
    except stripe.error.StripeError as e:
        cur.close()
        return jsonify({"error": f"Stripe error: {str(e.user_message)}"}), 500
    except Exception as e:
        cur.close()
        return jsonify({"error": f"Payment error: {str(e)}"}), 500

    # Store payment record as pending
    try:
        cur.execute(
            "INSERT INTO payments (user_id, booking_id, amount, currency, status, payment_gateway_order_id) VALUES (%s, %s, %s, 'INR', 'pending', %s)",
            (user_id, booking_id, amount_inr, intent["id"])
        )
        db.commit()
    except Exception as e:
        cur.close()
        return jsonify({"error": f"DB error storing payment record: {e}"}), 500
    cur.close()

    return jsonify({
        "client_secret": intent["client_secret"],
        "payment_intent_id": intent["id"],
        "booking_id": booking_id,
        "amount": amount_paise
    }), 200


# ---------- Confirm Stripe Payment ----------
@app.route("/confirm-payment", methods=["POST"])
def confirm_payment():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    payment_intent_id = data.get("payment_intent_id", "")
    booking_id = data.get("booking_id")

    if not payment_intent_id or not booking_id:
        return jsonify({"success": False, "error": "Missing parameters"}), 400

    try:
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    except stripe.error.StripeError as e:
        return jsonify({"success": False, "error": str(e.user_message)}), 400

    # Use a fresh pooled connection — completely isolated, never stale
    pay_db = None
    pay_cur = None
    db_success = False
    try:
        pay_db = get_conn()
        pay_cur = pay_db.cursor()
        if intent["status"] == "succeeded":
            pay_cur.execute(
                "UPDATE payments SET status='success', payment_gateway_payment_id=%s WHERE payment_gateway_order_id=%s",
                (payment_intent_id, payment_intent_id)
            )
            pay_cur.execute(
                "UPDATE user_bookings SET payment_status='paid' WHERE id=%s",
                (booking_id,)
            )
            pay_db.commit()
            db_success = True
        else:
            pay_cur.execute(
                "UPDATE payments SET status='failed' WHERE payment_gateway_order_id=%s",
                (payment_intent_id,)
            )
            pay_cur.execute(
                "UPDATE user_bookings SET payment_status='failed' WHERE id=%s",
                (booking_id,)
            )
            pay_db.commit()
    except Exception as e:
        print(f"[ERROR] confirm_payment DB update failed: {e}")
        if pay_db:
            try: pay_db.rollback()
            except: pass
        return jsonify({"success": False, "error": f"DB error: {e}. Your payment ID is {payment_intent_id}"}), 500
    finally:
        if pay_cur:
            try: pay_cur.close()
            except: pass
        if pay_db:
            try: pay_db.close()
            except: pass

    if intent["status"] != "succeeded":
        return jsonify({"success": False, "error": "Payment not completed. Please try again."}), 400

    if intent["status"] == "succeeded":
        # ---- Send confirmation emails (non-blocking) ----
        try:
            print(f"[MAIL] Preparing email data for booking_id={booking_id}", flush=True)
            mail_db = get_conn()
            ecur = mail_db.cursor(dictionary=True)
            ecur.execute("""
                SELECT
                    u.username  AS user_name,
                    u.email     AS user_email,
                    h.title     AS activity_title,
                    h.price     AS price,
                    hu.username AS host_name,
                    hu.email    AS host_email,
                    b.booking_date
                FROM user_bookings b
                JOIN users u         ON b.user_id      = u.id
                JOIN host_activity h ON b.activity_id  = h.id
                JOIN users hu        ON h.host_user_id = hu.id
                WHERE b.id = %s
            """, (booking_id,))
            info = ecur.fetchone()
            ecur.close()
            mail_db.close()

            if not info:
                print(f"[MAIL] WARNING: No booking data found for booking_id={booking_id} — emails not sent", flush=True)
            else:
                amount_inr = float(info["price"] or 0)
                print(f"[MAIL] Queued emails → user: {info['user_email']} | host: {info['host_email']}", flush=True)

                def _send_both(inf, amt, txn_id):
                    print(f"[MAIL] Thread starting — sending to user {inf['user_email']} and host {inf['host_email']}", flush=True)
                    # Send user email first
                    _send_brevo(
                        to_email  = inf["user_email"],
                        to_name   = inf["user_name"],
                        subject   = f"Booking Confirmed: {inf['activity_title']}",
                        html_body = build_user_email(
                            username       = inf["user_name"],
                            activity_title = inf["activity_title"],
                            booking_date   = str(inf["booking_date"]),
                            amount_inr     = amt,
                            transaction_id = txn_id,
                            host_name      = inf["host_name"],
                            host_email     = inf["host_email"],
                        ),
                    )
                    # Send host email second
                    _send_brevo(
                        to_email  = inf["host_email"],
                        to_name   = inf["host_name"],
                        subject   = f"New Paid Booking: {inf['activity_title']}",
                        html_body = build_host_email(
                            host_name      = inf["host_name"],
                            username       = inf["user_name"],
                            user_email     = inf["user_email"],
                            activity_title = inf["activity_title"],
                            booking_date   = str(inf["booking_date"]),
                            amount_inr     = amt,
                            transaction_id = txn_id,
                        ),
                    )

                t = threading.Thread(
                    target=_send_both,
                    args=(info, amount_inr, payment_intent_id),
                    daemon=False
                )
                t.start()
        except Exception as mail_err:
            print(f"[MAIL] Error preparing email data: {mail_err}", flush=True)
        # -------------------------------------------------

        flash("Payment successful! Your booking is confirmed.", "success")
        return jsonify({"success": True}), 200



# ---------- User Auth Routes ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        ensure_connection()
        username = request.form["username"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode("utf-8")
        try:
            cursor.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
                           (username, email, password))
            db.commit()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except Error as e:
            print(f"[ERROR] Registration Error: {e}")
            flash("Username or Email already exists!", "danger")
    return render_template("register.html")

from flask import session, redirect, url_for

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ensure_connection()
        email = request.form["email"]
        password = request.form["password"]

        cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
        user = cursor.fetchone()

        if user and bcrypt.check_password_hash(user["password"], password):

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            # 🔥 Check if user is a host
            cursor.execute(
                "SELECT COUNT(*) as count FROM host_activity WHERE host_user_id=%s",
                (user["id"],)
            )
            result = cursor.fetchone()

            if result["count"] > 0:
                session["is_host"] = True
                return redirect(url_for("host_dashboard"))
            else:
                session["is_host"] = False
                return redirect(url_for("dashboard"))

        flash("Invalid email or password!", "danger")

    return render_template("login.html")





@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out!", "info")
    return redirect(url_for("login"))


@app.route("/host/dashboard")
def host_dashboard():
    print("SESSION DATA:", session)
    if "user_id" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login"))

    host_user_id = session["user_id"]

    # 🔥 Check if this user has activities
    cursor.execute("SELECT COUNT(*) as count FROM host_activity WHERE host_user_id=%s", (host_user_id,))
    result = cursor.fetchone()

    if result["count"] == 0:
        flash("You are not a host yet. Create an activity first.", "warning")
        return redirect(url_for("home"))

    cursor.execute("""
        SELECT 
            h.id,
            h.title,
            h.category,
            h.image_filename,
            COUNT(b.id) AS total_bookings
        FROM host_activity h
        LEFT JOIN user_bookings b 
            ON h.id = b.activity_id
        WHERE h.host_user_id = %s
        GROUP BY h.id, h.title, h.category,h.image_filename
        ORDER BY h.created_at DESC
    """, (host_user_id,))

    activities = cursor.fetchall()

    return render_template("host_dashboard.html", activities=activities)

@app.route("/host/activity/<int:activity_id>")
def host_activity_bookings(activity_id):
    if "user_id" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login"))

    host_user_id = session["user_id"]

    cursor.execute("""
        SELECT 
            b.id,
            u.username,
            u.email,
            b.booking_date,
            b.payment_status
        FROM user_bookings b
        JOIN users u ON b.user_id = u.id
        JOIN host_activity h ON b.activity_id = h.id
        WHERE b.activity_id = %s
          AND h.host_user_id = %s
        ORDER BY b.booking_date DESC
    """, (activity_id, host_user_id))

    bookings = cursor.fetchall()

    return render_template(
        "host_activity_bookings.html",
        bookings=bookings,
        activity_id=activity_id
    )



# ---------- Delete Activity (host only) ----------
@app.route("/host/activity/<int:activity_id>/delete", methods=["POST"])
def delete_activity(activity_id):
    if "user_id" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login"))

    host_user_id = session["user_id"]
    cur = db.cursor(dictionary=True)

    # Verify ownership
    cur.execute("SELECT id FROM host_activity WHERE id=%s AND host_user_id=%s",
                (activity_id, host_user_id))
    if not cur.fetchone():
        cur.close()
        flash("Activity not found or you do not have permission to delete it.", "danger")
        return redirect(url_for("host_dashboard"))

    cur.execute("DELETE FROM host_activity WHERE id=%s AND host_user_id=%s",
                (activity_id, host_user_id))
    db.commit()
    cur.close()
    flash("Activity deleted successfully.", "success")
    return redirect(url_for("host_dashboard"))


# ---------- Unconfirm Booking (host only, non-paid) ----------
@app.route("/host/booking/<int:booking_id>/unconfirm", methods=["POST"])
def unconfirm_booking(booking_id):
    if "user_id" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login"))

    host_user_id = session["user_id"]
    activity_id = request.form.get("activity_id", type=int)
    cur = db.cursor(dictionary=True)

    # Verify the booking belongs to an activity owned by this host
    cur.execute("""
        SELECT b.id, b.payment_status
        FROM user_bookings b
        JOIN host_activity h ON b.activity_id = h.id
        WHERE b.id = %s AND h.host_user_id = %s
    """, (booking_id, host_user_id))
    booking = cur.fetchone()

    if not booking:
        cur.close()
        flash("Booking not found or you do not have permission.", "danger")
        return redirect(url_for("host_dashboard"))

    if booking["payment_status"] == "paid":
        cur.close()
        flash("Cannot unconfirm a paid booking.", "danger")
        return redirect(url_for("host_activity_bookings", activity_id=activity_id))

    cur.execute("DELETE FROM user_bookings WHERE id=%s", (booking_id,))
    db.commit()
    cur.close()
    flash("Booking has been unconfirmed and removed.", "success")
    return redirect(url_for("host_activity_bookings", activity_id=activity_id))


# ---------- Cancel Booking (user only, non-paid) ----------
@app.route("/booking/<int:booking_id>/cancel", methods=["POST"])
def cancel_booking(booking_id):
    if "user_id" not in session:
        flash("Please login first", "warning")
        return redirect(url_for("login"))

    user_id = session["user_id"]
    cur = db.cursor(dictionary=True)

    # Verify the booking belongs to this user
    cur.execute("SELECT id, payment_status FROM user_bookings WHERE id=%s AND user_id=%s",
                (booking_id, user_id))
    booking = cur.fetchone()

    if not booking:
        cur.close()
        flash("Booking not found.", "danger")
        return redirect(url_for("dashboard"))

    if booking["payment_status"] == "paid":
        cur.close()
        flash("Paid bookings cannot be cancelled.", "danger")
        return redirect(url_for("dashboard"))

    cur.execute("DELETE FROM user_bookings WHERE id=%s AND user_id=%s",
                (booking_id, user_id))
    db.commit()
    cur.close()
    flash("Booking cancelled successfully.", "success")
    return redirect(url_for("dashboard"))


# ── Image-Based Activity Recommendation ────────────────────────────────────
@app.route("/image-search", methods=["POST"])
def image_search():
    """
    Accepts a multipart/form-data POST with an image file.
    Uses CLIP to encode the image and returns the most semantically
    similar activities as JSON — completely separate from NLP text search.
    """
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"error": "No image uploaded."}), 400

    allowed_exts = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed_exts:
        return jsonify({"error": "Unsupported file type. Use JPG, PNG, WEBP or GIF."}), 400

    # Read & decode image
    try:
        img_bytes = file.read()
        img = PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
    except Exception as ex:
        return jsonify({"error": f"Could not read image: {ex}"}), 400

    # Encode image with CLIP → normalised unit vector
    img_emb = _get_clip_model().encode(img, convert_to_numpy=True,
                                       normalize_embeddings=True, show_progress_bar=False)

    # Score static activities: for each activity take MAX score across its prompts
    # (prompt ensemble — significantly improves per-activity accuracy)
    all_scored = []
    for prompt_embs, act in zip(_get_clip_static_embs(), STATIC_ACTIVITIES):
        # prompt_embs shape: (n_prompts, 512), img_emb shape: (512,)
        scores_per_prompt = prompt_embs @ img_emb          # shape (n_prompts,)
        best_score = float(scores_per_prompt.max())
        all_scored.append((best_score, act))

    # Score DB-hosted activities on-the-fly with multi-prompt fallback
    hosted = get_hosted_activities()
    for act in hosted:
        h_prompts = _get_clip_prompts(act)
        h_embs    = _encode_prompts(h_prompts)             # (n_prompts, 512)
        best_score = float((h_embs @ img_emb).max())
        all_scored.append((best_score, act))

    # ── Threshold & spread filter ──────────────────────────────────────────
    # CLIP_THRESHOLD (0.22): minimum score to count as a match.
    #   • Too low (≤0.18) = irrelevant activities shown.
    #   • Too high (≥0.26) = true matches blocked (e.g. bonfire → no result).
    #   0.22 is the sweet spot: catches related images while blocking noise.
    # SPREAD_MARGIN (0.03): within passing activities, drop any whose score
    #   is more than 0.03 below the single best match. Prevents a strong
    #   yoga match at 0.24 from dragging along unrelated activities at 0.22.
    # Smart fallback: if nothing passes 0.22 (very abstract image), show the
    #   single closest activity only if its score is ≥0.20 — so the user
    #   always gets a best-guess rather than a blank page.
    CLIP_THRESHOLD = 0.22
    SPREAD_MARGIN  = 0.015

    passing = [(s, act) for s, act in all_scored if s >= CLIP_THRESHOLD]
    if passing:
        ranked = sorted(passing, key=lambda x: x[0], reverse=True)[:4]
        top_score = ranked[0][0]
        ranked = [(s, act) for s, act in ranked if s >= top_score - SPREAD_MARGIN]
    else:
        # Smart top-1 fallback — nothing cleared 0.22, but show best guess
        # if it is at least loosely related (score >= 0.20).
        top = max(all_scored, key=lambda x: x[0])
        ranked = [(top[0], top[1])] if top[0] >= 0.20 else []

    results = []
    for score, act in ranked:
        results.append({
            "id":          act["id"],
            "title":       act["title"],
            "location":    act.get("location", ""),
            "price":       act.get("price", 0),
            "image_url":   act.get("image_url", "/static/images/home.png"),
            "category":    act.get("category", ""),
            "description": act.get("description", ""),
            "score":       round(float(score), 3),
        })

    return jsonify({"results": results})


# ---- Health ping (keeps Render free tier warm) ----
@app.route("/ping")
def ping():
    return "pong", 200


# ---- Admin: fix any stuck payments (pending in DB but succeeded in Stripe) ----
@app.route("/admin-fix-payment-lokly2026")
@app.route("/admin-fix-payment-lokly2026/<pi_id>")
def admin_fix_payment(pi_id=None):
    """
    If pi_id given  → fix that one payment.
    If no pi_id     → scan ALL pending payments and auto-fix any that succeeded in Stripe.
    """
    results = []
    try:
        fix_db = get_conn()
        fix_cur = fix_db.cursor(dictionary=True)

        if pi_id:
            # Fix a specific payment
            pis_to_check = [pi_id]
        else:
            # Find all pending payments and check them against Stripe
            fix_cur.execute(
                "SELECT payment_gateway_order_id FROM payments WHERE status='pending' ORDER BY id DESC LIMIT 50"
            )
            rows = fix_cur.fetchall()
            pis_to_check = [r["payment_gateway_order_id"] for r in rows if r["payment_gateway_order_id"]]

        for pi in pis_to_check:
            if not pi:
                continue
            # Check Stripe status
            try:
                intent = stripe.PaymentIntent.retrieve(pi)
                stripe_status = intent["status"]
            except Exception as se:
                results.append(f"SKIP {pi} — Stripe error: {se}")
                continue

            if stripe_status != "succeeded":
                results.append(f"SKIP {pi} — Stripe status: {stripe_status}")
                continue

            # Stripe says succeeded — look up booking and fix DB
            fix_cur.execute(
                "SELECT id, booking_id FROM payments WHERE payment_gateway_order_id=%s",
                (pi,)
            )
            row = fix_cur.fetchone()
            if not row:
                results.append(f"SKIP {pi} — no DB record found")
                continue

            booking_id = row["booking_id"]
            fix_cur.execute(
                "UPDATE payments SET status='success', payment_gateway_payment_id=%s WHERE payment_gateway_order_id=%s",
                (pi, pi)
            )
            fix_cur.execute(
                "UPDATE user_bookings SET payment_status='paid' WHERE id=%s",
                (booking_id,)
            )
            fix_db.commit()
            results.append(f"FIXED {pi} → booking {booking_id} marked paid")

        fix_cur.close()
        fix_db.close()
    except Exception as e:
        return f"Error: {e}", 500

    if not results:
        return "No pending payments found to fix.", 200
    return "<br>".join(results), 200


# ---------- Test Email Route (debug) ----------
@app.route("/test-email-lokly2026")
@app.route("/test-email-lokly2026/<to_email>")
def test_email_route(to_email=None):
    """Debug: verify Brevo SMTP is working. Visit /test-email-lokly2026/your@email.com"""
    if not to_email:
        return (
            f"<h3>Brevo Email Debug</h3>"
            f"<b>BREVO_SMTP_LOGIN:</b> {'SET (' + BREVO_SMTP_LOGIN[:6] + '...)' if BREVO_SMTP_LOGIN else 'NOT SET'}<br>"
            f"<b>BREVO_SMTP_KEY:</b> {'SET' if BREVO_SMTP_KEY else 'NOT SET'}<br>"
            f"<b>BREVO_FROM_EMAIL:</b> {BREVO_FROM_EMAIL or 'NOT SET'}<br><br>"
            f"Visit <code>/test-email-lokly2026/your@email.com</code> to send a test email."
        ), 200
    print(f"[TEST-EMAIL] Sending test email to {to_email}", flush=True)
    try:
        _send_brevo(
            to_email  = to_email,
            to_name   = "Test",
            subject   = "Lokly Email Test",
            html_body = "<h2>Lokly email is working!</h2><p>If you received this, Brevo SMTP is configured correctly.</p>",
        )
        return f"<h3>Test email sent to {to_email}</h3><p>Check Render logs for [MAIL] output.</p>", 200
    except Exception as e:
        return f"<h3>Error: {e}</h3>", 500


# ---------- Resend Booking Email Route (admin) ----------
@app.route("/resend-booking-email-lokly2026/<int:booking_id>")
def resend_booking_email(booking_id):
    """Manually resend booking confirmation emails for a specific booking_id."""
    try:
            rdb = get_conn()
            rcur = rdb.cursor(dictionary=True)
            rcur.execute("""
                SELECT
                    u.username  AS user_name,
                    u.email     AS user_email,
                    h.title     AS activity_title,
                    h.price     AS price,
                    hu.username AS host_name,
                    hu.email    AS host_email,
                    b.booking_date,
                    p.payment_gateway_order_id AS txn_id
                FROM user_bookings b
                JOIN users u         ON b.user_id      = u.id
                JOIN host_activity h ON b.activity_id  = h.id
                JOIN users hu        ON h.host_user_id = hu.id
                LEFT JOIN payments p ON p.booking_id   = b.id
                WHERE b.id = %s
            """, (booking_id,))
            info = rcur.fetchone()
            rcur.close()
            rdb.close()
    except Exception as e:
        return f"DB error: {e}", 500

    if not info:
        return f"No booking found for id={booking_id}", 404

    amt = float(info["price"] or 0)
    txn = info["txn_id"] or f"booking-{booking_id}"
    results = []

    # User email
    try:
        _send_brevo(
            to_email  = info["user_email"],
            to_name   = info["user_name"],
            subject   = f"Booking Confirmed: {info['activity_title']}",
            html_body = build_user_email(
                username=info["user_name"], activity_title=info["activity_title"],
                booking_date=str(info["booking_date"]), amount_inr=amt,
                transaction_id=txn, host_name=info["host_name"], host_email=info["host_email"],
            ),
        )
        results.append(f"User email sent to {info['user_email']}")
    except Exception as e:
        results.append(f"User email FAILED: {e}")

    # Host email
    try:
        _send_brevo(
            to_email  = info["host_email"],
            to_name   = info["host_name"],
            subject   = f"New Paid Booking: {info['activity_title']}",
            html_body = build_host_email(
                host_name=info["host_name"], username=info["user_name"],
                user_email=info["user_email"], activity_title=info["activity_title"],
                booking_date=str(info["booking_date"]), amount_inr=amt, transaction_id=txn,
            ),
        )
        results.append(f"Host email sent to {info['host_email']}")
    except Exception as e:
        results.append(f"Host email FAILED: {e}")

    return "<br>".join(results), 200


if __name__ == "__main__":
    print("   Lokly is running!")
    print("   Open this URL in your browser:")
    print("   http://127.0.0.1:5000")
    import socket as _socket
    try:
        _lan_ip = _socket.gethostbyname(_socket.gethostname())
    except Exception:
        _lan_ip = "YOUR_LAPTOP_IP"
    print(f"   http://{_lan_ip}:5000  (mobile/other devices)")
    print("="*45 + "\n")
    try:
        app.run(debug=True, host='0.0.0.0', use_reloader=False)
    except OSError as e:
        if "10048" in str(e) or "Address already in use" in str(e):
            print("\n[ERROR] Port 5000 is already in use.")
            print("Run this command first to free it:")
            print("   taskkill /F /IM python.exe")
            print("Then run app.py again.\n")
        else:
            raise