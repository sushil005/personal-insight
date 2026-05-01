from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = "your_secure_random_key_123"

DATABASE = Path(__file__).with_name("database.db")
IST_OFFSET = timedelta(hours=5, minutes=30)
VALID_MOODS = ("Happy", "Neutral", "Sad")


# ---------- DATABASE ----------
def get_db():
    return sqlite3.connect(DATABASE)


def init_db():
    with closing(get_db()) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                study_hours REAL DEFAULT 0,
                screen_time REAL DEFAULT 0,
                sleep_hours REAL DEFAULT 0,
                mood TEXT DEFAULT 'Neutral',
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        ensure_logs_schema(cursor)
        conn.commit()


def ensure_logs_schema(cursor):
    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(logs)").fetchall()
    }

    migrations = {
        "user_id": "ALTER TABLE logs ADD COLUMN user_id INTEGER",
        "study_hours": "ALTER TABLE logs ADD COLUMN study_hours REAL DEFAULT 0",
        "screen_time": "ALTER TABLE logs ADD COLUMN screen_time REAL DEFAULT 0",
        "sleep_hours": "ALTER TABLE logs ADD COLUMN sleep_hours REAL DEFAULT 0",
        "mood": "ALTER TABLE logs ADD COLUMN mood TEXT DEFAULT 'Neutral'",
        "date": "ALTER TABLE logs ADD COLUMN date TIMESTAMP",
    }

    for column, statement in migrations.items():
        if column not in columns:
            cursor.execute(statement)


def current_utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_hours(field_name, label):
    raw_value = request.form.get(field_name, "").strip()

    try:
        value = float(raw_value)
    except ValueError:
        flash(f"{label} must be a valid number.", "danger")
        return None

    if value < 0 or value > 24:
        flash(f"{label} must be between 0 and 24.", "danger")
        return None

    return value


def normalize_mood(mood):
    text = (mood or "Neutral").strip()

    for valid_mood in VALID_MOODS:
        if valid_mood.lower() in text.lower():
            return valid_mood

    return "Neutral"


def format_log_date(value):
    if not value:
        return ""

    text = str(value)
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")

    for date_format in formats:
        try:
            parsed = datetime.strptime(text, date_format)
            return (parsed + IST_OFFSET).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return (parsed + IST_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


init_db()


# ---------- HOME ----------
@app.route("/")
def home():
    return redirect(url_for("login"))


# ---------- REGISTER ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        try:
            with closing(get_db()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, password) VALUES (?, ?)",
                    (username, hashed_password),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            flash("Username already exists.", "danger")
            return redirect(url_for("register"))

        flash("Account created successfully!", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


# ---------- LOGIN ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username=?", (username,))
            user = cursor.fetchone()

        if user and check_password_hash(user[2], password):
            session["user"] = user[1]
            session["user_id"] = user[0]
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")
        return redirect(url_for("login"))

    return render_template("login.html")


# ---------- ADD LOG ----------
@app.route("/add", methods=["GET", "POST"])
def add_log():
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        study = parse_hours("study", "Study hours")
        screen = parse_hours("screen", "Screen time")
        sleep = parse_hours("sleep", "Sleep hours")
        mood = normalize_mood(request.form.get("mood"))

        if None in (study, screen, sleep):
            return redirect(url_for("add_log"))

        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO logs (
                    user_id, study_hours, screen_time, sleep_hours, mood, date
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session["user_id"],
                study,
                screen,
                sleep,
                mood,
                current_utc_timestamp(),
            ))
            conn.commit()

        flash("Log added successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_log.html", moods=VALID_MOODS)


# ---------- DELETE ----------
@app.route("/delete/<int:log_id>")
def delete_log(log_id):
    if "user" not in session:
        return redirect(url_for("login"))

    with closing(get_db()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM logs
            WHERE id=? AND user_id=?
        """, (log_id, session["user_id"]))
        conn.commit()

    flash("Log deleted successfully!", "success")
    return redirect(url_for("dashboard"))


# ---------- EDIT ----------
@app.route("/edit/<int:log_id>", methods=["GET", "POST"])
def edit_log(log_id):
    if "user" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        study = parse_hours("study", "Study hours")
        screen = parse_hours("screen", "Screen time")
        sleep = parse_hours("sleep", "Sleep hours")
        mood = normalize_mood(request.form.get("mood"))

        if None in (study, screen, sleep):
            return redirect(url_for("edit_log", log_id=log_id))

        with closing(get_db()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE logs
                SET study_hours=?, screen_time=?, sleep_hours=?, mood=?
                WHERE id=? AND user_id=?
            """, (study, screen, sleep, mood, log_id, session["user_id"]))
            conn.commit()

            if cursor.rowcount == 0:
                flash("Log not found.", "danger")
                return redirect(url_for("dashboard"))

        flash("Log updated successfully!", "success")
        return redirect(url_for("dashboard"))

    with closing(get_db()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT study_hours, screen_time, sleep_hours, mood
            FROM logs
            WHERE id=? AND user_id=?
        """, (log_id, session["user_id"]))
        log = cursor.fetchone()

    if log is None:
        flash("Log not found.", "danger")
        return redirect(url_for("dashboard"))

    log = (log[0], log[1], log[2], normalize_mood(log[3]))
    return render_template("edit_log.html", log=log, moods=VALID_MOODS)


# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    with closing(get_db()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, study_hours, screen_time, sleep_hours, mood, date
            FROM logs
            WHERE user_id=?
            ORDER BY date ASC, id ASC
        """, (session["user_id"],))
        logs = cursor.fetchall()

    updated_logs = []
    for log in logs:
        updated_logs.append((
            log[0],
            float(log[1]) if log[1] is not None else 0,
            float(log[2]) if log[2] is not None else 0,
            float(log[3]) if log[3] is not None else 0,
            normalize_mood(log[4]),
            format_log_date(log[5]),
        ))

    total_logs = len(updated_logs)

    if total_logs > 0:
        avg_study = sum(log[1] for log in updated_logs) / total_logs
        avg_screen = sum(log[2] for log in updated_logs) / total_logs
        avg_sleep = sum(log[3] for log in updated_logs) / total_logs
    else:
        avg_study = avg_screen = avg_sleep = 0

    if total_logs == 0:
        insight = "No data yet."
    else:
        insight = "Good progress"
        if avg_study < 2:
            insight = "Low productivity"
        if avg_screen > 7:
            insight += " | High screen time"

    dates = [log[5] for log in updated_logs]
    study_data = [log[1] for log in updated_logs]
    screen_data = [log[2] for log in updated_logs]
    sleep_data = [log[3] for log in updated_logs]

    return render_template(
        "dashboard.html",
        logs=updated_logs,
        total_logs=total_logs,
        avg_study=round(avg_study, 2),
        avg_screen=round(avg_screen, 2),
        avg_sleep=round(avg_sleep, 2),
        insight=insight,
        dates=dates,
        study_data=study_data,
        screen_data=screen_data,
        sleep_data=sleep_data,
    )


# ---------- LOGOUT ----------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- RUN ----------
if __name__ == "__main__":
    app.run()
