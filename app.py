import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, flash, jsonify
from flask_bcrypt import Bcrypt
import mysql.connector
from mysql.connector import Error
from werkzeug.utils import secure_filename
import os
import uuid
import stripe
from dotenv import load_dotenv
import math
import numpy as np
from sentence_transformers import SentenceTransformer

load_dotenv()

app = Flask(__name__)
app.secret_key = "lokly_secret_123"   # change to something secure
bcrypt = Bcrypt(app)

# -------- Stripe Setup --------
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
stripe.api_key = STRIPE_SECRET_KEY

# -------- MySQL Connection --------
try:
    db = mysql.connector.connect(
        host="localhost",
        user="root",        # your MySQL username
        password="DiyaP@2368",  # your MySQL password
        database="mywebsite"
    )
    cursor = db.cursor(dictionary=True)
    print("✅ MySQL Database connected successfully!")
except Error as e:
    print(f"❌ Error connecting to MySQL: {e}")
    db = None
    cursor = None

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
                status ENUM('pending','success','failed') DEFAULT 'pending',
                payment_gateway_order_id VARCHAR(255),
                payment_gateway_payment_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (booking_id) REFERENCES user_bookings(id) ON DELETE CASCADE
            )
        """)

        # 6️⃣ Add payment_status to user_bookings if not exists
        try:
            cursor.execute("""
                ALTER TABLE user_bookings
                ADD COLUMN payment_status ENUM('pending','paid','failed') DEFAULT 'pending'
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
     "category": "Adventure"}
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
            "image_url": f"/static/uploads/host_activity/{r['image_filename']}" if r.get("image_filename") else "/static/images/home.png",
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

def get_categories_from_db():
    """Read categories from the DB categories table."""
    try:
        c = db.cursor(dictionary=True)
        c.execute("SELECT name, slug FROM categories ORDER BY id")
        rows = c.fetchall()
        return [
            {"slug": r["slug"], "name": r["name"],
             "image": CATEGORY_IMAGES.get(r["slug"], "/static/images/home.png")}
            for r in rows
        ]
    except Exception:
        return [
            {"slug": "art-culture",   "name": "Art & Culture",  "image": "/static/images/art.jpg"},
            {"slug": "culinary-arts", "name": "Culinary Arts",  "image": "/static/images/pp7.png"},
            {"slug": "dance",         "name": "Dance",          "image": "/static/images/kathak.jpg"},
            {"slug": "wellness",      "name": "Wellness",       "image": "/static/images/yoga.jpg"},
            {"slug": "adventure",     "name": "Adventure",      "image": "/static/images/himtrek.jpg"},
        ]


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
print("Loading NLP model (all-MiniLM-L6-v2)...")
_nlp_model = SentenceTransformer('all-MiniLM-L6-v2')

def _activity_text(e):
    """Plain combined text for embedding — no generic hint words."""
    title       = e['title']
    description = e.get('description', '')
    category    = e.get('category', '')
    location    = e.get('location', '')
    return f"{title}. {description}. Category: {category}. Location: {location}".lower()

# Pre-compute normalized embeddings for static activities ONCE at startup
_static_texts      = [_activity_text(e) for e in STATIC_ACTIVITIES]
_static_embeddings = _nlp_model.encode(
    _static_texts,
    convert_to_numpy=True,
    normalize_embeddings=True,   # unit vectors → dot product == cosine similarity
)
print(f"NLP ready. {len(STATIC_ACTIVITIES)} static embeddings precomputed.")


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
                "image_url": (f"/static/uploads/host_activity/{r['image_filename']}"
                              if r.get("image_filename") else "/static/images/home.png"),
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
    Hybrid search: optional city filter (explicit or auto-detected) + semantic similarity.

    Pipeline
    --------
    Step 1  Build pool: static + DB-hosted activities.
    Step 2  If city not provided but query contains a known city name,
            auto-detect it and apply the same city filter.
    Step 3  If no query → return the (city-filtered) pool as-is.
    Step 4  Exact / partial title match → pinned to the top.
    Step 5  Semantic cosine similarity on the pool (threshold 0.35).
    Step 6  Merge without duplicates, cap at 5.
    """
    hosted   = get_hosted_activities()
    all_acts = list(STATIC_ACTIVITIES) + hosted

    # Step 2 – auto-detect city from query when not supplied explicitly
    if not city and query:
        city = extract_city_from_query(query, all_acts)

    # Apply city / location filter
    if city:
        city_lower = city.strip().lower()
        pool = [
            act for act in all_acts
            if city_lower in act.get("location", "").lower()
        ]
    else:
        pool = all_acts

    # Step 3 – no query: return city-filtered pool
    if not query:
        return pool

    ql = query.strip().lower()

    # Step 4 – exact / partial title matches within the pool (always shown first)
    exact_matches = [act for act in pool if ql in act["title"].lower()]

    try:
        # Step 5 – build normalised embedding matrix for the current pool
        if len(pool) == len(all_acts):
            # Full pool — reuse precomputed static embeddings
            if hosted:
                hosted_embs = _nlp_model.encode(
                    [_activity_text(e) for e in hosted],
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
                pool_embeddings = np.vstack([_static_embeddings, hosted_embs])
            else:
                pool_embeddings = _static_embeddings
        else:
            # City-filtered subset — encode only those activities
            pool_embeddings = _nlp_model.encode(
                [_activity_text(e) for e in pool],
                convert_to_numpy=True,
                normalize_embeddings=True,
            )

        # Encode query with the same normalisation
        q_emb = _nlp_model.encode(
            [ql],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        # Dot product of two unit vectors == cosine similarity
        scores = (q_emb @ pool_embeddings.T).flatten().tolist()

        # ── Strict Hybrid Scoring ──────────────────────────────────────────────
        query_words = ql.split()

        def keyword_boost(act):
            boost = 0.0
            title       = act["title"].lower()
            description = act.get("description", "").lower()
            category    = act.get("category", "").lower()
            for word in query_words:
                if word in title:
                    boost += 1.0        # strong — word in title
                elif word in category:
                    boost += 0.7        # medium — word in category
                elif word in description:
                    boost += 0.3        # small  — word in description
            return boost

        hybrid_scores = [
            (sem * 0.6 + keyword_boost(act) * 0.4, sem, keyword_boost(act), act)
            for sem, act in zip(scores, pool)
        ]

        # STRICT MODE: short queries (1–2 words) must match a keyword field
        STRICT_MODE = len(query_words) <= 2

        filtered = []
        for final_score, sem, boost, act in hybrid_scores:
            if STRICT_MODE:
                if boost > 0:                   # keyword must appear somewhere
                    filtered.append((final_score, act))
            else:
                if sem >= 0.40:                 # longer queries: require strong semantic match
                    filtered.append((final_score, act))

        # Fallback: if nothing passed the strict / semantic gate, use cosine >= 0.40
        if not filtered:
            filtered = [
                (fs, act)
                for fs, sem, boost, act in hybrid_scores
                if sem >= 0.40
            ]

        filtered.sort(key=lambda x: x[0], reverse=True)
        semantic_ranked = [act for _, act in filtered]

        # Step 6 – merge: exact matches first, then semantic fill, no duplicates
        seen_ids      = {act["id"] for act in exact_matches}
        final_results = list(exact_matches)

        for act in semantic_ranked:
            if act["id"] not in seen_ids:
                seen_ids.add(act["id"])
                final_results.append(act)

        return final_results[:5]

    except Exception as e:
        print(f"[search_experiences] error: {e}")
        # Keyword-only fallback if model fails
        return [
            act for act in pool
            if ql in act["title"].lower()
            or ql in act.get("location",    "").lower()
            or ql in act.get("description", "").lower()
            or ql in act.get("category",    "").lower()
        ][:5]


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
        # Create unique filename
        unique_name = uuid.uuid4().hex[:8]
        filename = secure_filename(image.filename)
        image_filename = f"{unique_name}_{filename}"

        # Save image
        image_path = os.path.join(HOST_IMG_FOLDER, image_filename)
        image.save(image_path)

    # ---------- INSERT INTO DATABASE ----------
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO host_activity
        (host_user_id, name, title, description, location, price, image_filename, category, session_link)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        host_user_id,
        name,
        title,
        description,
        location,
        price,
        image_filename,
        category,
        session_link
    ))

    db.commit()
    cursor.close()

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
        print(f"❌ Hosted experience not found for DB id {db_id} (full id {id})")  # Debug log
        return "Experience not found", 404
    exp = {
        "id": id,
        "title": rec["title"],
        "location": rec.get("location"),
        "price": float(rec["price"]) if rec["price"] is not None else 0,
        "image_url": f"/static/uploads/host_activity/{rec['image_filename']}" if rec.get("image_filename") else "/static/images/home.png",
        "description": rec.get("description", ""),
        "host_name": rec.get("name"),
        "session_link": rec.get("session_link"),
        "category": rec.get("category", "Other"),
        "is_hosted": True  # Flag for template (hosted = enroll button)
    }
    print(f"✅ Loading hosted experience: {exp['title']} (id {id}, DB {db_id})")  # Debug log
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
            "image_url": (f"/static/uploads/host_activity/{rec['image_filename']}"
                          if rec.get("image_filename") else "/static/images/home.png"),
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
    cur = db.cursor(dictionary=True)

    # Create booking with payment_status = pending
    cur.execute(
        "INSERT INTO user_bookings (user_id, activity_id, booking_date, payment_status) VALUES (%s, %s, %s, 'pending')",
        (user_id, db_activity_id, date)
    )
    db.commit()
    booking_id = cur.lastrowid

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
    cur.execute(
        "INSERT INTO payments (user_id, booking_id, amount, currency, status, payment_gateway_order_id) VALUES (%s, %s, %s, 'INR', 'pending', %s)",
        (user_id, booking_id, amount_inr, intent["id"])
    )
    db.commit()
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

    cur = db.cursor()

    if intent["status"] == "succeeded":
        cur.execute(
            "UPDATE payments SET status='success', payment_gateway_payment_id=%s WHERE payment_gateway_order_id=%s",
            (payment_intent_id, payment_intent_id)
        )
        cur.execute(
            "UPDATE user_bookings SET payment_status='paid' WHERE id=%s",
            (booking_id,)
        )
        db.commit()
        cur.close()
        flash("Payment successful! Your booking is confirmed.", "success")
        return jsonify({"success": True}), 200
    else:
        cur.execute(
            "UPDATE payments SET status='failed' WHERE payment_gateway_order_id=%s",
            (payment_intent_id,)
        )
        cur.execute(
            "UPDATE user_bookings SET payment_status='failed' WHERE id=%s",
            (booking_id,)
        )
        db.commit()
        cur.close()
        return jsonify({"success": False, "error": "Payment not completed. Please try again."}), 400



# ---------- User Auth Routes ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
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
            print(f"❌ Registration Error: {e}")
            flash("Username or Email already exists!", "danger")
    return render_template("register.html")

from flask import session, redirect, url_for

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
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
        bookings=bookings
    )



if __name__ == "__main__":
    print("\n" + "="*45)
    print("   Lokly is running!")
    print("   Open this URL in your browser:")
    print("   http://127.0.0.1:5000")
    print("="*45 + "\n")
    app.run(debug=True, use_reloader=False)