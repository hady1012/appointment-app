import os
import base64
import json
import re
import secrets
import smtplib
import ssl
from datetime import date, timedelta, datetime
from email.message import EmailMessage
from urllib.parse import urlparse, urlencode
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import pool
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "x8sK29!akL#92jF@pQz")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
SESSION_KEEP_ALIVE = timedelta(days=7)
app.permanent_session_lifetime = SESSION_KEEP_ALIVE
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Jerusalem"))
STORE_OPTIONAL_SCHEMA_READY = False
OWNER_SESSION_SCHEMA_READY = False
OWNER_SESSION_TIMEOUT = timedelta(hours=12)
OWNER_SESSION_CHECK_INTERVAL = timedelta(minutes=2)
PERFORMANCE_INDEXES_READY = False
AVAILABLE_SLOTS_CACHE = {}
AVAILABLE_SLOTS_CACHE_TTL = 20
ASSET_VERSION = "20260602-clock-share-reminders"
REMINDER_TRAFFIC_LAST_RUN = None
REMINDER_TRAFFIC_INTERVAL = timedelta(minutes=3)
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
KEEP_IMAGE_PREFIX = "__keep_image_"
DB_POOL = None
REMINDER_OPTIONS = {
    15: "about 15 minutes",
    30: "about 30 minutes",
    60: "about 1 hour",
    120: "about 2 hours",
    1440: "about 1 day",
}

DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
DAY_LABELS = {
    "sunday": "יום ראשון",
    "monday": "יום שני",
    "tuesday": "יום שלישי",
    "wednesday": "יום רביעי",
    "thursday": "יום חמישי",
    "friday": "יום שישי",
    "saturday": "יום שבת",
}


class PooledConnection:
    def __init__(self, connection, connection_pool):
        self._connection = connection
        self._pool = connection_pool
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        if not self._closed:
            try:
                self._connection.rollback()
            except Exception:
                pass
            self._pool.putconn(self._connection)
            self._closed = True


def get_connection():
    global DB_POOL
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    if DB_POOL is None:
        DB_POOL = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=int(os.environ.get("DATABASE_POOL_SIZE", "5")),
            dsn=database_url,
            connect_timeout=5,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
        )

    return PooledConnection(DB_POOL.getconn(), DB_POOL)


def now_local():
    return datetime.now(APP_TIMEZONE).replace(tzinfo=None)


def slugify_store_name(name):
    slug = re.sub(r"[^\w\u0590-\u05FF\u0600-\u06FF]+", "-", (name or "").strip().lower(), flags=re.UNICODE)
    slug = slug.strip("-")
    return slug or "business"


def store_details_url(store_id, store_name=None, external=False):
    if store_name:
        return url_for("store_details_by_slug", store_slug=slugify_store_name(store_name), _external=external)
    return url_for("store_details", store_id=store_id, _external=external)


@app.context_processor
def inject_asset_version():
    return {"asset_version": ASSET_VERSION, "store_details_url": store_details_url}


EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
TEXT_PATTERN = re.compile(r"^[\w\s\u0590-\u05FF.'\"()&+,-]+$", re.UNICODE)
PERSON_NAME_PATTERN = re.compile(r"^[A-Za-z\u0590-\u05FF\s.'-]+$", re.UNICODE)


def normalize_email(email):
    return (email or "").strip().lower()


def is_valid_email(email):
    email = normalize_email(email)
    if not email or len(email) > 254 or not EMAIL_PATTERN.match(email):
        return False

    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain or ".." in email:
        return False

    blocked_domains = {
        "example.com",
        "test.com",
        "fake.com",
        "mailinator.com",
        "tempmail.com",
        "10minutemail.com",
    }
    return domain not in blocked_domains


def normalize_phone(phone):
    phone = (phone or "").strip()
    if phone.startswith("+"):
        return "+" + re.sub(r"\D", "", phone[1:])
    return re.sub(r"\D", "", phone)


def is_valid_phone(phone):
    normalized = normalize_phone(phone)
    digits = normalized[1:] if normalized.startswith("+") else normalized

    if not digits or len(digits) < 9 or len(digits) > 15:
        return False

    if len(set(digits)) <= 2:
        return False

    return (
        normalized.startswith("+9725")
        or normalized.startswith("+972")
        or (normalized.startswith("05") and len(normalized) == 10)
        or (normalized.startswith("0") and len(normalized) in [9, 10])
    )


def clean_text(value, min_len=2, max_len=255):
    value = " ".join((value or "").strip().split())
    if len(value) < min_len or len(value) > max_len:
        return None
    if not TEXT_PATTERN.match(value):
        return None
    return value


def clean_person_name(value):
    value = " ".join((value or "").strip().split())
    if len(value) < 3 or len(value) > 80:
        return None
    if not PERSON_NAME_PATTERN.match(value):
        return None
    return value


def clean_optional_text(value, max_len=500):
    value = " ".join((value or "").strip().split())
    if not value:
        return ""
    if len(value) > max_len or not TEXT_PATTERN.match(value):
        return None
    return value


def clean_optional_coordinate(value, min_value, max_value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        coordinate = float(value)
    except ValueError:
        return None
    if coordinate < min_value or coordinate > max_value:
        return None
    return coordinate


def clean_reminder_minutes(value):
    try:
        reminder_minutes = int(value)
    except (TypeError, ValueError):
        return 30
    return reminder_minutes if reminder_minutes in REMINDER_OPTIONS else 30


def is_valid_image_url(value):
    value = (value or "").strip()
    if not value or len(value) > 900000:
        return False

    if value.startswith("data:image/"):
        header, separator, payload = value.partition(",")
        return (
            separator == ","
            and ";base64" in header
            and any(header.startswith(f"data:image/{kind}") for kind in ["jpeg", "jpg", "png", "webp"])
            and bool(payload)
        )

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    path = parsed.path.lower()
    image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    return path.endswith(image_extensions) or any(host in parsed.netloc.lower() for host in ["images.unsplash.com", "res.cloudinary.com"])


def allowed_image_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_store_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    filename = secure_filename(file_storage.filename)
    if not allowed_image_file(filename):
        raise ValueError("Please upload jpg, png, or webp photos only.")

    extension = filename.rsplit(".", 1)[1].lower()
    mime_extension = "jpeg" if extension == "jpg" else extension
    image_bytes = file_storage.read()
    if not image_bytes:
        return ""

    image_data = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/{mime_extension};base64,{image_data}"
    if not is_valid_image_url(data_url):
        raise ValueError("This photo is too large. Please upload a smaller jpg, png, or webp photo.")

    return data_url


def build_store_image_list(existing_urls, uploaded_files, stored_urls=None):
    image_urls = []
    uploaded_files = list(uploaded_files or [])
    existing_urls = list(existing_urls or [])
    stored_urls = list(stored_urls or [])

    for index in range(5):
        uploaded_file = uploaded_files[index] if index < len(uploaded_files) else None
        if uploaded_file and uploaded_file.filename:
            image_urls.append(save_store_image(uploaded_file))
            continue

        existing_url = (existing_urls[index] if index < len(existing_urls) else "").strip()
        if existing_url:
            if existing_url.startswith(KEEP_IMAGE_PREFIX):
                stored_image = stored_urls[index] if index < len(stored_urls) else ""
                if stored_image:
                    image_urls.append(stored_image)
                continue

            if existing_url.startswith("/static/uploads/store_photos/") or is_valid_image_url(existing_url):
                image_urls.append(existing_url)
            else:
                raise ValueError("One of the saved photos is not valid. Please upload it again.")

    return image_urls[:5]


def validate_store_images(raw_urls):
    cleaned = []
    seen = set()

    for raw_url in raw_urls[:5]:
        image_url = (raw_url or "").strip()
        if not image_url:
            continue
        if not is_valid_image_url(image_url):
            return None, "Please upload valid image files. Use jpg, png, or webp photos."
        if image_url not in seen:
            cleaned.append(image_url)
            seen.add(image_url)

    return cleaned, None


def parse_json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def validate_service_inputs(service_names, service_prices, service_durations):
    valid_services = []

    for name, price, duration in zip(service_names, service_prices, service_durations):
        clean_name = clean_text(name, min_len=2, max_len=80)
        if not clean_name and not price.strip() and not duration.strip():
            continue

        if not clean_name:
            return None, "יש להזין שם שירות תקין."

        try:
            price_value = float(price)
            duration_value = int(duration)
        except ValueError:
            return None, "מחיר או משך שירות אינם תקינים."

        if price_value < 0 or price_value > 10000:
            return None, "מחיר השירות אינו תקין."

        if duration_value < 5 or duration_value > 480:
            return None, "משך השירות חייב להיות בין 5 ל-480 דקות."

        valid_services.append((clean_name, price_value, duration_value))

    if not valid_services:
        return None, "יש להוסיף לפחות שירות אחד תקין."

    return valid_services, None


def email_configured():
    brevo_ready = bool(os.environ.get("BREVO_API_KEY") and os.environ.get("BREVO_SENDER_EMAIL"))
    resend_ready = bool(os.environ.get("RESEND_API_KEY") and os.environ.get("MAIL_FROM"))
    smtp_ready = all(os.environ.get(key) for key in ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM"])
    return brevo_ready or resend_ready or smtp_ready


def send_brevo_email(to_email, subject, body):
    api_key = os.environ.get("BREVO_API_KEY")
    sender_email = os.environ.get("BREVO_SENDER_EMAIL")
    sender_name = os.environ.get("BREVO_SENDER_NAME", os.environ.get("MAIL_FROM_NAME", "Appointment Booking"))

    if not api_key or not sender_email:
        return False

    payload = json.dumps(
        {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }
    ).encode("utf-8")

    request = urlrequest.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "appointment-booking/1.0",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(request, timeout=12) as response:
            return 200 <= response.status < 300
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        app.logger.warning("Brevo email failed: %s %s", exc.code, error_body)
        return False
    except URLError as exc:
        app.logger.warning("Brevo email failed: %s", exc.reason)
        return False


def send_resend_email(to_email, subject, body):
    api_key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("MAIL_FROM")
    sender_name = os.environ.get("MAIL_FROM_NAME", "Appointment Booking")

    if not api_key or not mail_from:
        return False

    payload = json.dumps(
        {
            "from": f"{sender_name} <{mail_from}>",
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")

    request = urlrequest.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "appointment-booking/1.0",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(request, timeout=12) as response:
            return 200 <= response.status < 300
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        app.logger.warning("Resend email failed: %s %s", exc.code, error_body)
        return False
    except URLError as exc:
        app.logger.warning("Resend email failed: %s", exc.reason)
        return False


def send_email(to_email, subject, body):
    if not to_email or not email_configured():
        return False

    if os.environ.get("BREVO_API_KEY"):
        return send_brevo_email(to_email, subject, body)

    if os.environ.get("RESEND_API_KEY"):
        return send_resend_email(to_email, subject, body)

    if not all(os.environ.get(key) for key in ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM"]):
        return False

    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]
    mail_from = os.environ["MAIL_FROM"]
    sender_name = os.environ.get("MAIL_FROM_NAME", "Appointment Booking")

    message = EmailMessage()
    message["From"] = f"{sender_name} <{mail_from}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=12) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=12) as server:
                server.starttls(context=context)
                server.login(smtp_username, smtp_password)
                server.send_message(message)
        return True
    except Exception as exc:
        app.logger.warning("Email failed: %s", exc)
        return False


def ensure_email_schema(cursor):
    cursor.execute(
        """
        ALTER TABLE appointments
        ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP
        """
    )


def ensure_rating_schema(cursor):
    cursor.execute(
        """
        ALTER TABLE ratings
        ALTER COLUMN appointment_id DROP NOT NULL
        """
    )


def ensure_store_optional_schema(cursor):
    global STORE_OPTIONAL_SCHEMA_READY
    if STORE_OPTIONAL_SCHEMA_READY:
        return

    try:
        cursor.execute(
            """
            ALTER TABLE stores
            ADD COLUMN IF NOT EXISTS location TEXT
            """
        )
        cursor.execute(
            """
            ALTER TABLE stores
            ADD COLUMN IF NOT EXISTS image_urls TEXT
            """
        )
        cursor.execute(
            """
            ALTER TABLE stores
            ADD COLUMN IF NOT EXISTS location_lat DOUBLE PRECISION
            """
        )
        cursor.execute(
            """
            ALTER TABLE stores
            ADD COLUMN IF NOT EXISTS location_lng DOUBLE PRECISION
            """
        )
        cursor.execute(
            """
            ALTER TABLE stores
            ADD COLUMN IF NOT EXISTS reminder_minutes_before INT DEFAULT 30
            """
        )
        ensure_performance_indexes(cursor)
        cursor.connection.commit()
    except Exception:
        cursor.connection.rollback()
        raise

    STORE_OPTIONAL_SCHEMA_READY = True


def ensure_performance_indexes(cursor):
    global PERFORMANCE_INDEXES_READY
    if PERFORMANCE_INDEXES_READY:
        return

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_owner_id ON stores(owner_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stores_category ON stores(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_services_store_id ON services(store_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_services_store_duration ON services(store_id, duration_minutes, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_working_hours_store_day ON working_hours(store_id, day_of_week)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_store_date_time ON appointments(store_id, appointment_date, appointment_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_store_date_time_desc ON appointments(store_id, appointment_date DESC, appointment_time DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_customer_id ON appointments(customer_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ratings_store_status ON ratings(store_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ratings_store_status_created ON ratings(store_id, status, created_at DESC)")
    PERFORMANCE_INDEXES_READY = True


def ensure_password_reset_schema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_codes (
            id SERIAL PRIMARY KEY,
            user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            attempts INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_password_reset_codes_user_active
        ON password_reset_codes(user_id, expires_at, used_at)
        """
    )


def ensure_owner_session_schema(cursor):
    global OWNER_SESSION_SCHEMA_READY
    if OWNER_SESSION_SCHEMA_READY:
        return

    try:
        cursor.execute(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS active_owner_session_token TEXT
            """
        )
        cursor.execute(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS active_owner_session_seen_at TIMESTAMP
            """
        )
        ensure_performance_indexes(cursor)
        cursor.connection.commit()
    except Exception:
        cursor.connection.rollback()
        raise

    OWNER_SESSION_SCHEMA_READY = True


def build_appointment_email_body(appointment, intro):
    return f"""{intro}

Business: {appointment['store_name']}
Service: {appointment['service_name']}
Date: {appointment['date']}
Time: {appointment['time']}
Customer: {appointment['customer_name']}
Phone: {appointment['customer_phone']}

Open the business page:
{appointment['store_url']}

Thank you for trusting Golan Pick. We wish you the best time in our store.
"""


def send_booking_emails(appointment):
    customer_subject = f"Appointment confirmed: {appointment['store_name']} on {appointment['date']} at {appointment['time']}"
    customer_body = build_appointment_email_body(
        appointment,
        "Your appointment was booked successfully.",
    )
    owner_subject = f"New appointment at {appointment['store_name']}"
    owner_body = build_appointment_email_body(
        appointment,
        "A customer booked an appointment in your store.",
    )

    customer_sent = send_email(appointment.get("customer_email"), customer_subject, customer_body)
    owner_sent = send_email(appointment.get("owner_email"), owner_subject, owner_body)
    return customer_sent, owner_sent


def send_due_reminder_emails(limit=50):
    if not email_configured():
        return {"sent": 0, "email_configured": False}

    conn = get_connection()
    cursor = conn.cursor()
    sent_count = 0
    current_time = now_local()
    window_end = current_time + timedelta(minutes=10)

    try:
        ensure_email_schema(cursor)
        ensure_store_optional_schema(cursor)
        cursor.execute(
            """
            SELECT a.id, a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time,
                   cu.email, st.name, sv.name, ou.email, st.id,
                   COALESCE(st.reminder_minutes_before, 30)
            FROM appointments a
            JOIN users cu ON cu.id = a.customer_id
            JOIN stores st ON st.id = a.store_id
            JOIN users ou ON ou.id = st.owner_id
            LEFT JOIN services sv ON sv.id = a.service_id
            WHERE a.reminder_sent_at IS NULL
              AND (a.appointment_date + a.appointment_time) > %s
              AND (
                  (a.appointment_date + a.appointment_time)
                  - (COALESCE(st.reminder_minutes_before, 30) * INTERVAL '1 minute')
              ) <= %s
            ORDER BY a.appointment_date, a.appointment_time
            LIMIT %s
            """,
            (current_time, window_end, limit),
        )
        rows = cursor.fetchall()

        for row in rows:
            appointment = {
                "id": row[0],
                "customer_name": row[1],
                "customer_phone": row[2],
                "date": str(row[3]),
                "time": str(row[4])[:5],
                "customer_email": row[5],
                "store_name": row[6],
                "service_name": row[7] or "",
                "owner_email": row[8],
                "store_url": store_details_url(row[9], row[6], external=True),
                "reminder_minutes": row[10],
            }
            reminder_label = REMINDER_OPTIONS.get(appointment["reminder_minutes"], "soon")
            subject = f"Reminder: appointment at {appointment['time']} today"
            body = build_appointment_email_body(
                appointment,
                f"Reminder: your appointment starts in {reminder_label}.",
            )
            if send_email(appointment["customer_email"], subject, body):
                sent_count += 1
                cursor.execute(
                    "UPDATE appointments SET reminder_sent_at = %s WHERE id = %s",
                    (now_local(), appointment["id"]),
                )

        conn.commit()
        return {
            "sent": sent_count,
            "email_configured": True,
            "window_start": current_time,
            "window_end": window_end,
        }
    finally:
        cursor.close()
        conn.close()


def maybe_send_due_reminders_from_traffic():
    global REMINDER_TRAFFIC_LAST_RUN
    if request.endpoint in {"static", "available_slots", "send_reminders"}:
        return
    if not email_configured():
        return

    current_time = now_local()
    if REMINDER_TRAFFIC_LAST_RUN and current_time - REMINDER_TRAFFIC_LAST_RUN < REMINDER_TRAFFIC_INTERVAL:
        return

    REMINDER_TRAFFIC_LAST_RUN = current_time
    try:
        send_due_reminder_emails(limit=20)
    except Exception as exc:
        app.logger.warning("Automatic reminder check failed: %s", exc)


def send_password_reset_email(user_email, full_name, reset_code):
    subject = "Your My Marketplace password reset code"
    body = f"""Hi {full_name},

We received a request to reset your My Marketplace password.

Your verification code is: {reset_code}

This code expires in 15 minutes. If you did not ask to reset your password, you can ignore this email.
"""
    return send_email(user_email, subject, body)


@app.before_request
def keep_recent_users_signed_in():
    maybe_send_due_reminders_from_traffic()

    if request.endpoint in {"static", "available_slots"}:
        return None

    if not session.get("user_id"):
        return None

    current_time = now_local()
    last_activity = session.get("last_activity_at")

    if session.permanent and last_activity:
        try:
            if current_time - datetime.fromisoformat(last_activity) > SESSION_KEEP_ALIVE:
                session.clear()
                flash("Your session expired after a week away. Please log in again.")
                return redirect(url_for("login"))
        except ValueError:
            session.clear()
            return redirect(url_for("login"))

    session["last_activity_at"] = current_time.isoformat()
    session.modified = True
    return None


@app.before_request
def guard_owner_single_device_session():
    if request.endpoint in {"static", "login", "logout", "available_slots"}:
        return None

    if session.get("role") != "owner" or not session.get("user_id") or not session.get("owner_session_token"):
        return None

    current_time = now_local()
    last_check = session.get("owner_session_last_check")
    if last_check:
        try:
            if current_time - datetime.fromisoformat(last_check) < OWNER_SESSION_CHECK_INTERVAL:
                return None
        except ValueError:
            pass

    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_owner_session_schema(cursor)
        cursor.execute(
            """
            SELECT active_owner_session_token
            FROM users
            WHERE id = %s
            """,
            (session["user_id"],),
        )
        row = cursor.fetchone()

        if not row or row[0] != session.get("owner_session_token"):
            session.clear()
            flash("Oops, your business account was opened on another device. To use it here, log out there and log in again.")
            return redirect(url_for("login"))

        last_touch = session.get("owner_session_last_touch")
        should_touch = True
        if last_touch:
            try:
                should_touch = now_local() - datetime.fromisoformat(last_touch) > timedelta(minutes=5)
            except ValueError:
                should_touch = True

        if should_touch:
            cursor.execute(
                """
                UPDATE users
                SET active_owner_session_seen_at = %s
                WHERE id = %s AND active_owner_session_token = %s
                """,
                (current_time, session["user_id"], session["owner_session_token"]),
            )
            conn.commit()
            session["owner_session_last_touch"] = current_time.isoformat()

        session["owner_session_last_check"] = current_time.isoformat()
    finally:
        cursor.close()
        conn.close()

    return None


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    flash("The photos were too large. Please choose fewer or smaller photos and try again.")
    return redirect(request.referrer or url_for("work"))


def get_day_name_from_date(date_str):
    day_index = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    mapping = {
        6: "sunday",
        0: "monday",
        1: "tuesday",
        2: "wednesday",
        3: "thursday",
        4: "friday",
        5: "saturday",
    }
    return mapping[day_index]


def time_to_minutes(time_value):
    if isinstance(time_value, str):
        try:
            dt = datetime.strptime(time_value, "%H:%M")
        except ValueError:
            dt = datetime.strptime(time_value, "%H:%M:%S")
        return dt.hour * 60 + dt.minute
    return time_value.hour * 60 + time_value.minute


def minutes_to_time_string(minutes):
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def today_range():
    min_date = now_local().date()
    max_date = min_date + timedelta(days=7)
    return min_date.isoformat(), max_date.isoformat()


def normalize_category_name(raw_value):
    return " ".join((raw_value or "").strip().split())


def get_categories(cursor=None):
    internal_conn = None
    internal_cursor = cursor

    try:
        if internal_cursor is None:
            internal_conn = get_connection()
            internal_cursor = internal_conn.cursor()

        internal_cursor.execute("SELECT name FROM business_categories ORDER BY LOWER(name)")
        rows = internal_cursor.fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        if internal_conn:
            internal_cursor.close()
            internal_conn.close()


def ensure_category_exists(category_name, owner_id=None, cursor=None):
    category_name = normalize_category_name(category_name)
    if not category_name:
        return ""

    internal_cursor = cursor
    internal_conn = None

    try:
        if internal_cursor is None:
            internal_conn = get_connection()
            internal_cursor = internal_conn.cursor()

        internal_cursor.execute(
            """
            INSERT INTO business_categories (name, created_by_owner_id)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (category_name, owner_id),
        )

        if internal_conn:
            internal_conn.commit()

        return category_name
    finally:
        if internal_conn:
            internal_cursor.close()
            internal_conn.close()


def generate_available_slots(store_id, service_id, appointment_date, cursor=None):
    internal_conn = None
    internal_cursor = cursor

    try:
        if internal_cursor is None:
            internal_conn = get_connection()
            internal_cursor = internal_conn.cursor()

        try:
            requested_date = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        except ValueError:
            return []

        current_dt = now_local()
        current_date = current_dt.date()

        if requested_date < current_date:
            return []

        day_name = get_day_name_from_date(appointment_date)

        internal_cursor.execute(
            """
            SELECT s.duration_minutes, wh.is_open, wh.start_time, wh.end_time
            FROM services s
            JOIN working_hours wh
              ON wh.store_id = s.store_id
            WHERE s.id = %s
              AND s.store_id = %s
              AND wh.day_of_week = %s
            """,
            (service_id, store_id, day_name),
        )
        row = internal_cursor.fetchone()
        if not row:
            return []

        service_duration, is_open, start_time, end_time = row

        if not is_open or not start_time or not end_time:
            return []

        service_duration = int(service_duration)
        start_minutes = time_to_minutes(start_time)
        end_minutes = time_to_minutes(end_time)

        internal_cursor.execute(
            """
            SELECT a.appointment_time, s.duration_minutes
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.store_id = %s AND a.appointment_date = %s
            """,
            (store_id, appointment_date),
        )
        existing_rows = internal_cursor.fetchall()

        busy_ranges = []
        for appointment_time, existing_duration in existing_rows:
            existing_start = time_to_minutes(appointment_time)
            existing_end = existing_start + int(existing_duration)
            busy_ranges.append((existing_start, existing_end))

        slots = []
        current = start_minutes
        current_minutes = current_dt.hour * 60 + current_dt.minute

        while current + service_duration <= end_minutes:
            candidate_start = current
            candidate_end = current + service_duration

            if requested_date == current_date and candidate_start <= current_minutes:
                current += 15
                continue

            overlaps = any(
                not (candidate_end <= busy_start or candidate_start >= busy_end)
                for busy_start, busy_end in busy_ranges
            )

            if not overlaps:
                slots.append(minutes_to_time_string(candidate_start))

            current += 15

        return slots
    finally:
        if internal_conn:
            internal_cursor.close()
            internal_conn.close()


def cached_available_slots(store_id, service_id, appointment_date):
    key = (int(store_id), str(service_id), str(appointment_date))
    current_time = now_local()
    cached = AVAILABLE_SLOTS_CACHE.get(key)
    if cached and current_time - cached["created_at"] < timedelta(seconds=AVAILABLE_SLOTS_CACHE_TTL):
        return list(cached["slots"])

    slots = generate_available_slots(store_id, service_id, appointment_date)
    AVAILABLE_SLOTS_CACHE[key] = {"created_at": current_time, "slots": list(slots)}

    if len(AVAILABLE_SLOTS_CACHE) > 200:
        cutoff = current_time - timedelta(seconds=AVAILABLE_SLOTS_CACHE_TTL)
        stale_keys = [cache_key for cache_key, value in AVAILABLE_SLOTS_CACHE.items() if value["created_at"] < cutoff]
        for cache_key in stale_keys:
            AVAILABLE_SLOTS_CACHE.pop(cache_key, None)

    return slots


def clear_available_slots_cache(store_id=None):
    if store_id is None:
        AVAILABLE_SLOTS_CACHE.clear()
        return

    store_id = int(store_id)
    for cache_key in list(AVAILABLE_SLOTS_CACHE):
        if cache_key[0] == store_id:
            AVAILABLE_SLOTS_CACHE.pop(cache_key, None)


ASSISTANT_STOPWORDS = {
    "find", "search", "business", "businesses", "store", "stores", "appointment", "time", "available",
    "for", "me", "please", "near", "in", "on", "the", "a", "an", "do", "does", "have", "has", "can",
    "i", "want", "need", "book", "pick", "tell", "show", "open", "today", "tomorrow", "sunday",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "what", "about", "when",
    "free", "slot", "slots", "there", "is", "are",
    "all", "every", "area", "type", "kind", "category", "categories", "around",
    "חפש", "מחפש", "לחפש", "עסק", "עסקים", "תור", "תורים", "זמין", "זמינים", "אפשר", "לי", "אני", "רוצה", "צריך",
    "מה", "ומה", "לגבי", "יש", "האם",
    "ביום", "היום", "מחר", "ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת",
    "ابحث", "عمل", "اعمال", "موعد", "متاح", "اليوم", "غدا", "ماذا", "عن", "هل", "يوجد",
}

ASSISTANT_DAY_WORDS = {
    "sunday": "sunday", "ראשון": "sunday", "الاحد": "sunday", "الأحد": "sunday",
    "monday": "monday", "שני": "monday", "الاثنين": "monday", "الإثنين": "monday",
    "tuesday": "tuesday", "שלישי": "tuesday", "الثلاثاء": "tuesday",
    "wednesday": "wednesday", "רביעי": "wednesday", "الاربعاء": "wednesday", "الأربعاء": "wednesday",
    "thursday": "thursday", "חמישי": "thursday", "الخميس": "thursday",
    "friday": "friday", "שישי": "friday", "الجمعة": "friday",
    "saturday": "saturday", "שבת": "saturday", "السبت": "saturday",
}

ASSISTANT_PREFERENCE_WORDS = {
    "cheap": "cheapest", "cheaper": "cheapest", "cheapest": "cheapest", "price": "cheapest",
    "fast": "fastest", "faster": "fastest", "fastest": "fastest", "quick": "fastest", "short": "fastest",
    "best": "best", "recommended": "best", "recommend": "best",
    "זול": "cheapest", "זולה": "cheapest", "זולים": "cheapest", "מחיר": "cheapest", "הכי זול": "cheapest",
    "מהיר": "fastest", "מהירה": "fastest", "קצר": "fastest", "קצרה": "fastest", "הכי מהר": "fastest",
    "מומלץ": "best", "מומלצת": "best", "הכי טוב": "best",
    "رخيص": "cheapest", "ارخص": "cheapest", "أرخص": "cheapest", "سعر": "cheapest",
    "سريع": "fastest", "اسرع": "fastest", "أسرع": "fastest",
    "افضل": "best", "أفضل": "best",
}

ASSISTANT_FOLLOWUP_WORDS = {
    "them", "those", "these", "it", "there", "that", "same", "option", "options",
    "what about", "how about", "which", "one",
    "אותם", "אלה", "זה", "שם", "אותו", "אותה", "מה לגבי", "ומה", "איזה", "אחד",
    "هذه", "هؤلاء", "نفس", "ماذا عن", "اي", "أي",
}


ASSISTANT_MAX_CARDS = 30
ASSISTANT_LOCATION_ALIASES = {
    "telaviv": ["tel aviv", "tel-aviv", "telaviv"],
    "tel aviv": ["tel aviv", "tel-aviv", "telaviv"],
    "buqata": ["buqata", "buq'ata", "buqata", "bukata", "buk'ata", "buqatha", "buqatah"],
    "buq ata": ["buq ata", "buq'ata", "buqata", "buqata"],
    "bukata": ["bukata", "buqata", "buq'ata", "buqata"],
}


def assistant_requested_date(message):
    normalized = (message or "").lower()
    today = now_local().date()

    if any(word in normalized for word in ["tomorrow", "מחר", "غدا", "غداً"]):
        return today + timedelta(days=1), "tomorrow"

    if any(word in normalized for word in ["today", "היום", "اليوم"]):
        return today, "today"

    for raw_word, day_name in ASSISTANT_DAY_WORDS.items():
        if raw_word in normalized:
            target_index = DAYS.index(day_name)
            current_day = get_day_name_from_date(today.isoformat())
            current_index = DAYS.index(current_day)
            days_ahead = (target_index - current_index) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead), day_name

    return None, ""


def assistant_unique_tokens(tokens, limit=16):
    unique_tokens = []
    for token in tokens:
        token = str(token or "").strip().lower()
        if token and token not in unique_tokens:
            unique_tokens.append(token)
        if len(unique_tokens) >= limit:
            break
    return unique_tokens


def assistant_expand_location_tokens(tokens):
    expanded = []
    for token in tokens:
        normalized = str(token or "").strip().lower()
        if not normalized:
            continue
        collapsed = normalized.replace("-", "").replace("'", "").replace(" ", "")
        expanded.append(normalized)
        for alias_key, aliases in ASSISTANT_LOCATION_ALIASES.items():
            alias_collapsed = alias_key.replace("-", "").replace("'", "").replace(" ", "")
            if collapsed == alias_collapsed or normalized == alias_key:
                expanded.extend(aliases)
    return assistant_unique_tokens(expanded, limit=24)


def assistant_search_tokens(message):
    normalized = (message or "").lower().replace("-", " ")
    normalized = normalized.replace("telaviv", "tel aviv").replace("תל-אביב", "תל אביב")
    raw_tokens = re.findall(r"[\w\u0590-\u05FF\u0600-\u06FF]+", normalized, flags=re.UNICODE)
    tokens = []

    for token in raw_tokens:
        if len(token) < 2 or token in ASSISTANT_STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)

    if "tel" in tokens and "aviv" in tokens:
        tokens.append("tel aviv")
    if "תל" in tokens and "אביב" in tokens:
        tokens.append("תל אביב")

    return assistant_unique_tokens(tokens, limit=8)


def assistant_preferences(message):
    normalized = (message or "").lower()
    preferences = []
    for phrase, preference in ASSISTANT_PREFERENCE_WORDS.items():
        if phrase in normalized and preference not in preferences:
            preferences.append(preference)
    return preferences


def assistant_extract_price_limit(message):
    normalized = (message or "").lower()
    patterns = [
        r"(?:under|below|up to|max|maximum)\s*(?:₪|nis|ils)?\s*(\d{1,5})",
        r"(?:עד|מקסימום|פחות מ)\s*₪?\s*(\d{1,5})",
        r"(?:اقل من|حتى|حد اقصى)\s*(\d{1,5})",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


def assistant_should_use_context(message, tokens, context_store_ids):
    if not context_store_ids:
        return False
    normalized = (message or "").lower()
    if any(word in normalized for word in ASSISTANT_FOLLOWUP_WORDS):
        return True
    if assistant_preferences(message):
        return True
    if not tokens:
        return True
    preference_values = set(ASSISTANT_PREFERENCE_WORDS)
    return all(token in preference_values for token in tokens)


def assistant_text_score(text, tokens):
    normalized = (text or "").lower()
    score = 0
    for token in tokens:
        if not token:
            continue
        if normalized == token:
            score += 8
        elif normalized.startswith(token):
            score += 5
        elif token in normalized:
            score += 3
    return score


def assistant_language(message, fallback=""):
    text = message or ""
    fallback_text = (fallback or "").lower()
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    if re.search(r"[\u0590-\u05FF]", text):
        return "he"
    if fallback_text.startswith("ar"):
        return "ar"
    if fallback_text.startswith("he") or fallback_text.startswith("iw"):
        return "he"
    return "en"


def assistant_is_small_talk(message):
    normalized = (message or "").strip().lower()
    normalized = re.sub(r"[^\w\u0590-\u05FF\u0600-\u06FF\s]", "", normalized, flags=re.UNICODE)
    small_talk_phrases = {
        "hi", "hello", "hey", "thanks", "thank you",
        "bye", "goodbye", "see you",
        "שלום", "היי", "הי", "תודה", "תודה רבה", "ביי", "להתראות",
        "مرحبا", "اهلا", "أهلا", "شكرا", "مع السلامة", "باي",
    }
    if normalized in small_talk_phrases:
        return True
    return any(phrase in normalized for phrase in ["thank you", "תודה", "ביי", "شكرا"])


def assistant_is_capability_question(message):
    normalized = (message or "").strip().lower()
    return any(
        phrase in normalized
        for phrase in [
            "what can you do", "how can you help", "help me", "how does this work",
            "מה אתה יודע", "איך אתה יכול", "עזרה", "מה אפשר",
            "ماذا تستطيع", "كيف تساعد", "مساعدة", "شو بتقدر",
        ]
    )


def assistant_hello_reply(language):
    if language == "he":
        return "שלום. כתוב מה אתה מחפש, למשל: שטיפת רכב בתל אביב, או תור פנוי ביום ראשון."
    if language == "ar":
        return "مرحبا. اكتب ما تبحث عنه، مثلا: غسيل سيارات في تل أبيب، أو موعد متاح يوم الأحد."
    return "Hi. Tell me what you need, for example: car wash in Tel Aviv, or a free time on Sunday."


def assistant_small_talk_reply(message, language):
    normalized = (message or "").strip().lower()
    if any(word in normalized for word in ["bye", "goodbye", "ביי", "להתראות", "مع السلامة", "باي"]):
        if language == "he":
            return "בשמחה. אם תרצה לחפש עסק או תור אחר, אני כאן."
        if language == "ar":
            return "على الرحب والسعة. إذا أردت البحث عن عمل أو موعد آخر، أنا هنا."
        return "You are welcome. If you want to find another business or time, I am here."
    return assistant_hello_reply(language)


def assistant_text(language, key, **kwargs):
    texts = {
        "empty": {
            "en": "Hi, I am the Golan Pick assistant. Ask me for a business, city, service, or available time.",
            "he": "היי, אני העוזר של Golan Pick. אפשר לשאול אותי על עסק, עיר, שירות או שעה פנויה.",
            "ar": "مرحبا، أنا مساعد Golan Pick. اسألني عن عمل، مدينة، خدمة أو موعد متاح.",
        },
        "capabilities": {
            "en": "I can search businesses by city, category, or service, check live availability, remember the options we just discussed, and help you continue with follow-up questions like: what about tomorrow?",
            "he": "אני יכול לחפש עסקים לפי עיר, קטגוריה או שירות, לבדוק זמינות בזמן אמת, לזכור את האפשרויות שדיברנו עליהן, ולהמשיך עם שאלות המשך כמו: ומה לגבי מחר?",
            "ar": "يمكنني البحث عن أعمال حسب المدينة أو الفئة أو الخدمة، فحص المواعيد المتاحة مباشرة، تذكر الخيارات التي تحدثنا عنها، والمتابعة بأسئلة مثل: وماذا عن الغد؟",
        },
        "need_more_details": {
            "en": "Sure. What kind of business are you looking for, and in which city? For example: barber in Haifa, car wash in Tel Aviv, or private lesson tomorrow.",
            "he": "בשמחה. איזה סוג עסק אתה מחפש ובאיזו עיר? למשל: מספרה בחיפה, שטיפת רכב בתל אביב, או שיעור פרטי מחר.",
            "ar": "أكيد. ما نوع العمل الذي تبحث عنه وفي أي مدينة؟ مثلا: حلاق في حيفا، غسيل سيارات في تل أبيب، أو درس خاص غدا.",
        },
        "too_long": {
            "en": "Please write a shorter, clearer request.",
            "he": "כתוב בקשה קצרה וברורה יותר בבקשה.",
            "ar": "اكتب طلبا أقصر وأوضح من فضلك.",
        },
        "db_down": {
            "en": "The assistant needs the live database to search businesses. Please try again when the service is connected.",
            "he": "העוזר צריך את הדאטהבייס החי כדי לחפש עסקים. נסה שוב כשהשירות מחובר.",
            "ar": "المساعد يحتاج قاعدة البيانات المباشرة للبحث عن الأعمال. حاول مرة أخرى عند اتصال الخدمة.",
        },
        "no_results": {
            "en": "I could not find a matching business yet. Try a service, city, or category, for example: car wash in Tel Aviv.",
            "he": "לא מצאתי עסק מתאים עדיין. נסה שירות, עיר או קטגוריה, למשל: שטיפת רכב בתל אביב.",
            "ar": "لم أجد عملا مناسبا بعد. جرّب خدمة أو مدينة أو فئة، مثلا: غسيل سيارات في تل أبيب.",
        },
        "found_open": {
            "en": "I found {count} business option with available times for {date}.",
            "he": "מצאתי {count} אפשרויות עם שעות פנויות ל־{date}.",
            "ar": "وجدت {count} خيارات فيها مواعيد متاحة في {date}.",
        },
        "found_closed": {
            "en": "I found matching businesses, but no open time for {date}. Try another day.",
            "he": "מצאתי עסקים מתאימים, אבל אין שעה פנויה ל־{date}. נסה יום אחר.",
            "ar": "وجدت أعمالا مناسبة، لكن لا يوجد موعد متاح في {date}. جرّب يوما آخر.",
        },
        "found_businesses": {
            "en": "I found these businesses. Ask me about a day, for example: do they have time on Sunday?",
            "he": "מצאתי את העסקים האלה. אפשר לשאול על יום, למשל: יש להם זמן ביום ראשון?",
            "ar": "وجدت هذه الأعمال. اسألني عن يوم، مثلا: هل يوجد وقت يوم الأحد؟",
        },
        "customer_rule": {
            "en": " I can help you choose one business at a time so owners do not receive duplicate bookings.",
            "he": " אני יכול לעזור לבחור עסק אחד בכל פעם כדי שלא יהיו הזמנות כפולות לבעלי עסקים.",
            "ar": " يمكنني مساعدتك في اختيار عمل واحد كل مرة حتى لا تصل حجوزات مكررة لأصحاب الأعمال.",
        },
        "login_rule": {
            "en": " Log in as a customer before booking a time.",
            "he": " כדי לקבוע תור צריך להתחבר כלקוח.",
            "ar": " سجّل الدخول كزبون قبل حجز موعد.",
        },
    }
    return texts[key].get(language, texts[key]["en"]).format(**kwargs)


def assistant_general_reply(message, language):
    normalized = (message or "").strip().lower()
    question_words = ("?", "what", "how", "why", "can you", "do you", "help", "price", "cost", "login", "sign up", "register")
    if not any(word in normalized for word in question_words):
        return ""

    if any(word in normalized for word in ["price", "cost", "pay", "payment"]):
        return "Business owners add their own services, prices, and durations. Search by area or business type and I will show the matching businesses with their available service details."
    if any(word in normalized for word in ["login", "sign up", "register", "account"]):
        return "Customers can create an account to book appointments. Business owners can create an owner account, add a business, services, working hours, photos, and manage bookings."
    if any(word in normalized for word in ["cancel", "change appointment", "reschedule"]):
        return "For appointment changes, open the business page or your appointments area. If you tell me the business, service, city, or day, I can help you find the right option again."
    if any(word in normalized for word in ["availability", "available", "free time", "slot", "hours"]):
        return "I can check live availability from the businesses in Golan Pick. Try a request like: barber in Buqata tomorrow, car wash in Tel Aviv, or show businesses in Buqata."
    if any(word in normalized for word in ["golan pick", "site", "app", "platform"]):
        return "Golan Pick helps customers find local businesses and book available appointments, while business owners manage services, working hours, bookings, reminders, and customer ratings."
    if language == "he":
        return "I can help with Golan Pick questions and search the live business list. Write an area like Buqata or Tel Aviv, then write the business type or service you want."
    if language == "ar":
        return "I can help with Golan Pick questions and search the live business list. Write an area like Buqata or Tel Aviv, then write the business type or service you want."
    return "I can help with Golan Pick questions and search the live business list. Write an area like Buqata or Tel Aviv, then write the business type or service you want."


def assistant_date_label(date_value, language):
    day_labels = {
        "en": {
            "sunday": "Sunday", "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday",
            "thursday": "Thursday", "friday": "Friday", "saturday": "Saturday",
        },
        "he": {
            "sunday": "יום ראשון", "monday": "יום שני", "tuesday": "יום שלישי", "wednesday": "יום רביעי",
            "thursday": "יום חמישי", "friday": "יום שישי", "saturday": "שבת",
        },
        "ar": {
            "sunday": "الأحد", "monday": "الاثنين", "tuesday": "الثلاثاء", "wednesday": "الأربعاء",
            "thursday": "الخميس", "friday": "الجمعة", "saturday": "السبت",
        },
    }
    day_name = get_day_name_from_date(date_value.isoformat())
    day_label = day_labels.get(language, day_labels["en"]).get(day_name, day_name)
    if language == "he":
        return f"{day_label}, {date_value.strftime('%d/%m')}"
    if language == "ar":
        return f"{day_label}، {date_value.strftime('%d/%m')}"
    return f"{day_label}, {date_value.strftime('%d %B')}"


def assistant_location_condition(tokens):
    conditions = []
    params = []
    for token in assistant_expand_location_tokens(tokens):
        conditions.append("st.location ILIKE %s")
        params.append(f"%{token}%")
    return " OR ".join(conditions), params


def assistant_tokens_look_like_area(tokens, cursor):
    if not tokens:
        return False
    condition, params = assistant_location_condition(tokens)
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM stores st
        WHERE COALESCE(st.location, '') <> ''
          AND ({condition})
        """,
        tuple(params),
    )
    return (cursor.fetchone() or [0])[0] > 0


def assistant_find_stores(message, cursor, area_tokens=None, limit=ASSISTANT_MAX_CARDS):
    tokens = assistant_search_tokens(message)
    search_tokens = assistant_unique_tokens(tokens + assistant_expand_location_tokens(tokens), limit=24)
    area_tokens = list(area_tokens or [])
    params = []

    query = """
        SELECT DISTINCT st.id, st.name, st.category, COALESCE(st.description, ''),
               COALESCE(st.location, '')
        FROM stores st
        LEFT JOIN services sv ON sv.store_id = st.id
    """

    where_clauses = []

    if area_tokens:
        location_clause, location_params = assistant_location_condition(area_tokens)
        where_clauses.append(f"({location_clause})")
        params.extend(location_params)

    if search_tokens:
        conditions = []
        for token in search_tokens:
            conditions.append(
                """
                (
                    st.name ILIKE %s OR st.category ILIKE %s OR st.description ILIKE %s
                    OR st.location ILIKE %s OR sv.name ILIKE %s
                )
                """
            )
            like_token = f"%{token}%"
            params.extend([like_token, like_token, like_token, like_token, like_token])
        where_clauses.append("(" + " OR ".join(conditions) + ")")

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += f" ORDER BY st.id DESC LIMIT {max(40, int(limit))}"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    if not search_tokens:
        return rows[:limit]

    def row_score(row):
        _store_id, name, category, description, location = row
        return (
            assistant_text_score(name, tokens) * 3
            + assistant_text_score(category, tokens) * 2
            + assistant_text_score(location, tokens) * 2
            + assistant_text_score(description, tokens)
        )

    return sorted(rows, key=row_score, reverse=True)[:limit]


def assistant_context_store_ids(raw_ids):
    ids = []
    if not isinstance(raw_ids, list):
        return ids
    for raw_id in raw_ids:
        try:
            store_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if store_id > 0 and store_id not in ids:
            ids.append(store_id)
        if len(ids) >= ASSISTANT_MAX_CARDS:
            break
    return ids


def assistant_context_tokens(raw_tokens):
    tokens = []
    if not isinstance(raw_tokens, list):
        return tokens
    for raw_token in raw_tokens:
        token = str(raw_token or "").strip().lower()
        if not token or len(token) > 60:
            continue
        if not re.match(r"^[\w\u0590-\u05FF\u0600-\u06FF ]+$", token, flags=re.UNICODE):
            continue
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= 8:
            break
    return tokens


def assistant_find_stores_by_ids(store_ids, cursor):
    if not store_ids:
        return []
    placeholders = ", ".join(["%s"] * len(store_ids))
    cursor.execute(
        f"""
        SELECT DISTINCT st.id, st.name, st.category, COALESCE(st.description, ''),
               COALESCE(st.location, '')
        FROM stores st
        WHERE st.id IN ({placeholders})
        ORDER BY st.id DESC
        LIMIT 30
        """,
        tuple(store_ids),
    )
    return cursor.fetchall()


def assistant_store_services(store_id, cursor):
    cursor.execute(
        """
        SELECT id, name, price, duration_minutes
        FROM services
        WHERE store_id = %s
        ORDER BY id
        """,
        (store_id,),
    )
    return cursor.fetchall()


def assistant_choose_service(services, message, tokens):
    if not services:
        return None
    preferences = assistant_preferences(message)
    price_limit = assistant_extract_price_limit(message)

    def service_score(service):
        _service_id, name, price, duration = service
        score = assistant_text_score(name, tokens) * 4
        price_value = float(price or 0)
        duration_value = int(duration or 0)
        if price_limit is not None:
            score += 10 if price_value <= price_limit else -12
        if "cheapest" in preferences:
            score -= price_value / 20
        if "fastest" in preferences:
            score -= duration_value / 10
        if "best" in preferences:
            score += 2
        return score

    return sorted(services, key=service_score, reverse=True)[0]


def assistant_card_reason(language, card, preferences):
    bits = []
    if card.get("service"):
        if language == "he":
            bits.append(f"שירות מתאים: {card['service']}")
        elif language == "ar":
            bits.append(f"خدمة مناسبة: {card['service']}")
        else:
            bits.append(f"Matching service: {card['service']}")
    if card.get("location"):
        if language == "he":
            bits.append(f"מיקום: {card['location']}")
        elif language == "ar":
            bits.append(f"الموقع: {card['location']}")
        else:
            bits.append(f"Location: {card['location']}")
    if "cheapest" in preferences and card.get("price") is not None:
        if language == "he":
            bits.append(f"מחיר: ₪{card['price']}")
        elif language == "ar":
            bits.append(f"السعر: ₪{card['price']}")
        else:
            bits.append(f"Price: ₪{card['price']}")
    if "fastest" in preferences and card.get("duration"):
        if language == "he":
            bits.append(f"משך: {card['duration']} דקות")
        elif language == "ar":
            bits.append(f"المدة: {card['duration']} دقيقة")
        else:
            bits.append(f"Duration: {card['duration']} min")
    return " · ".join(bits[:3])


def assistant_smart_reply(language, cards, requested_date, preferences, area_tokens=None, filtered_by_area=False, area_only=False):
    best = cards[0] if cards else {}
    area_text = " ".join(area_tokens or [])
    if requested_date:
        available_count = sum(1 for card in cards if card["slots"])
        date_text = assistant_date_label(requested_date, language)
        if available_count:
            base = assistant_text(language, "found_open", count=available_count, date=date_text)
        else:
            base = assistant_text(language, "found_closed", date=date_text)
    elif filtered_by_area and area_text:
        if language == "he":
            base = f"מצאתי {len(cards)} עסקים באזור {area_text}. עכשיו אפשר לכתוב סוג עסק או שירות, למשל: מספרה, שטיפת רכב, יופי, שיעור פרטי."
        elif language == "ar":
            base = f"وجدت {len(cards)} أعمال في منطقة {area_text}. اكتب نوع العمل أو الخدمة مثل: حلاق، غسيل سيارات، تجميل، درس خاص."
        else:
            categories = assistant_unique_tokens([str(card.get("category", "")).strip() for card in cards if card.get("category")], limit=6)
            category_hint = f" Types here include: {', '.join(categories)}." if categories else ""
            base = f"I found {len(cards)} businesses in {area_text}.{category_hint} Now tell me the business type or service, like barber, car wash, beauty, or private lesson."
        if filtered_by_area and area_text and not area_only:
            base = f"I found {len(cards)} matching options in {area_text}. You can open one, ask for available times, or try another business type."
    else:
        base = assistant_text(language, "found_businesses")

    if not best:
        return base

    if language == "he":
        if "cheapest" in preferences and best.get("price") is not None:
            return f"{base} האפשרות הראשונה נראית הכי משתלמת: {best['name']} עם {best['service']} במחיר ₪{best['price']}."
        if "fastest" in preferences and best.get("duration"):
            return f"{base} שמתי קודם את {best['name']} כי השירות קצר יחסית ({best['duration']} דקות)."
        return f"{base} שמתי את {best['name']} ראשון כי הוא הכי קרוב למה שביקשת."
    if language == "ar":
        if "cheapest" in preferences and best.get("price") is not None:
            return f"{base} الخيار الأول يبدو أوفر: {best['name']} مع {best['service']} بسعر ₪{best['price']}."
        if "fastest" in preferences and best.get("duration"):
            return f"{base} وضعت {best['name']} أولا لأن الخدمة قصيرة نسبيا ({best['duration']} دقيقة)."
        return f"{base} وضعت {best['name']} أولا لأنه الأقرب لطلبك."
    if "cheapest" in preferences and best.get("price") is not None:
        return f"{base} The first option looks like the best price: {best['name']} with {best['service']} for ₪{best['price']}."
    if "fastest" in preferences and best.get("duration"):
        return f"{base} I put {best['name']} first because the service is relatively quick ({best['duration']} minutes)."
    return f"{base} I put {best['name']} first because it best matches your request."


@app.route("/assistant/chat", methods=["POST"])
def assistant_chat():
    payload = request.get_json(silent=True) or {}
    message = " ".join(str(payload.get("message", "") or "").strip().split())
    language = assistant_language(message, payload.get("language", ""))
    context_store_ids = assistant_context_store_ids(payload.get("context_store_ids", []))
    context_area_tokens = assistant_context_tokens(payload.get("context_area_tokens", []))
    if len(message) > 500:
        return jsonify({"reply": assistant_text(language, "too_long"), "cards": [], "language": language}), 400

    if not message:
        return jsonify({
            "reply": assistant_text(language, "empty"),
            "cards": [],
            "language": language,
        })

    if assistant_is_small_talk(message):
        return jsonify({
            "reply": assistant_small_talk_reply(message, language),
            "cards": [],
            "language": language,
        })

    if assistant_is_capability_question(message):
        return jsonify({
            "reply": assistant_text(language, "capabilities"),
            "cards": [],
            "language": language,
        })

    requested_date, date_label = assistant_requested_date(message)
    search_tokens = assistant_search_tokens(message)
    preferences = assistant_preferences(message)
    has_area_context = bool(context_area_tokens)
    use_area_filter = has_area_context and bool(search_tokens) and not assistant_should_use_context(message, search_tokens, context_store_ids)
    use_context = assistant_should_use_context(message, search_tokens, context_store_ids) and bool(context_store_ids)
    if not requested_date and not search_tokens and not context_store_ids and not has_area_context:
        return jsonify({
            "reply": assistant_text(language, "need_more_details"),
            "cards": [],
            "language": language,
        })

    try:
        conn = get_connection()
    except RuntimeError:
        general_reply = assistant_general_reply(message, language)
        if general_reply:
            return jsonify({
                "reply": general_reply,
                "cards": [],
                "language": language,
                "context_area_tokens": context_area_tokens,
            })
        return jsonify({
            "reply": assistant_text(language, "db_down"),
            "cards": [],
            "language": language,
        }), 503

    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        is_area_only_search = False
        active_area_tokens = []
        if use_context:
            store_rows = assistant_find_stores_by_ids(context_store_ids, cursor)
            active_area_tokens = context_area_tokens
        elif use_area_filter:
            active_area_tokens = context_area_tokens
            store_rows = assistant_find_stores(message, cursor, area_tokens=active_area_tokens, limit=ASSISTANT_MAX_CARDS)
        else:
            is_area_only_search = assistant_tokens_look_like_area(search_tokens, cursor)
            active_area_tokens = search_tokens if is_area_only_search else []
            store_rows = assistant_find_stores(message, cursor, limit=ASSISTANT_MAX_CARDS)
        cards = []

        for store_id, name, category, description, location in store_rows:
            services = assistant_store_services(store_id, cursor)
            service = assistant_choose_service(services, message, search_tokens)
            slots = []

            if requested_date and service:
                slots = generate_available_slots(store_id, service[0], requested_date.isoformat(), cursor)[:3]

            store_url = url_for("store_details", store_id=store_id)
            if requested_date and service and slots:
                store_url = f"{store_url}?{urlencode({
                    'service_id': service[0],
                    'appointment_date': requested_date.isoformat(),
                    'appointment_time': slots[0],
                })}"

            cards.append({
                "id": store_id,
                "name": name,
                "category": category,
                "description": description[:140],
                "location": location,
                "service": service[1] if service else "",
                "price": float(service[2]) if service else None,
                "duration": service[3] if service else None,
                "date": requested_date.isoformat() if requested_date else "",
                "slots": slots,
                "url": store_url,
            })

        def card_score(card):
            score = 0
            combined_text = " ".join([
                str(card.get("name", "")),
                str(card.get("category", "")),
                str(card.get("description", "")),
                str(card.get("location", "")),
                str(card.get("service", "")),
            ])
            score += assistant_text_score(combined_text, search_tokens)
            if requested_date and card.get("slots"):
                score += 25
            if "cheapest" in preferences and card.get("price") is not None:
                score -= float(card["price"]) / 10
            if "fastest" in preferences and card.get("duration"):
                score -= int(card["duration"]) / 5
            return score

        cards = sorted(cards, key=card_score, reverse=True)[:ASSISTANT_MAX_CARDS]
        for card in cards:
            card["reason"] = assistant_card_reason(language, card, preferences)
    finally:
        cursor.close()
        conn.close()

    if not cards:
        general_reply = assistant_general_reply(message, language)
        if general_reply:
            return jsonify({
                "reply": general_reply,
                "cards": [],
                "language": language,
                "context_area_tokens": context_area_tokens,
            })
        return jsonify({
            "reply": assistant_text(language, "no_results"),
            "cards": [],
            "language": language,
        })

    reply = assistant_smart_reply(
        language,
        cards,
        requested_date,
        preferences,
        area_tokens=active_area_tokens,
        filtered_by_area=bool(active_area_tokens) and not requested_date,
        area_only=is_area_only_search,
    )

    if not session.get("user_id"):
        reply += assistant_text(language, "login_rule")

    return jsonify({
        "reply": reply,
        "cards": cards,
        "language": language,
        "context_store_ids": [card["id"] for card in cards],
        "context_area_tokens": active_area_tokens,
    })


@app.errorhandler(404)
def not_found_error(_error):
    return render_template(
        "error.html",
        title="העמוד לא נמצא",
        message="לא מצאנו את העמוד שחיפשת. אפשר לחזור לעמוד הבית ולהמשיך משם.",
    ), 404


@app.errorhandler(500)
def internal_error(_error):
    return render_template(
        "error.html",
        title="משהו השתבש",
        message="הבקשה לא הושלמה כרגע. נסה לרענן את העמוד או לחזור לעמוד הבית.",
    ), 500


def get_store_calendar_days(store_id, cursor=None):
    internal_conn = None
    internal_cursor = cursor

    try:
        if internal_cursor is None:
            internal_conn = get_connection()
            internal_cursor = internal_conn.cursor()

        internal_cursor.execute(
            """
            SELECT id, duration_minutes
            FROM services
            WHERE store_id = %s
            ORDER BY duration_minutes ASC, id ASC
            LIMIT 1
            """,
            (store_id,),
        )
        service_row = internal_cursor.fetchone()

        start_date = now_local().date()
        end_date = start_date + timedelta(days=8)
        working_hours_by_day = {}
        appointments_by_date = {}

        if service_row:
            internal_cursor.execute(
                """
                SELECT day_of_week, is_open, start_time, end_time
                FROM working_hours
                WHERE store_id = %s
                """,
                (store_id,),
            )
            working_hours_by_day = {
                row[0]: {"is_open": row[1], "start_time": row[2], "end_time": row[3]}
                for row in internal_cursor.fetchall()
            }

            internal_cursor.execute(
                """
                SELECT a.appointment_date, a.appointment_time, s.duration_minutes
                FROM appointments a
                JOIN services s ON a.service_id = s.id
                WHERE a.store_id = %s
                  AND a.appointment_date >= %s
                  AND a.appointment_date < %s
                """,
                (store_id, start_date, end_date),
            )
            for appointment_date, appointment_time, duration in internal_cursor.fetchall():
                appointments_by_date.setdefault(appointment_date, []).append((appointment_time, duration))

        days = []
        for offset in range(8):
            d = start_date + timedelta(days=offset)
            d_iso = d.isoformat()
            day_name = get_day_name_from_date(d_iso)

            if not service_row:
                status = "closed"
            else:
                hours = working_hours_by_day.get(day_name)
                if not hours or not hours["is_open"] or not hours["start_time"] or not hours["end_time"]:
                    status = "busy"
                else:
                    service_duration = int(service_row[1])
                    start_minutes = time_to_minutes(hours["start_time"])
                    end_minutes = time_to_minutes(hours["end_time"])
                    current_dt = now_local()
                    current_minutes = current_dt.hour * 60 + current_dt.minute
                    busy_ranges = [
                        (time_to_minutes(appointment_time), time_to_minutes(appointment_time) + int(duration))
                        for appointment_time, duration in appointments_by_date.get(d, [])
                    ]
                    status = "busy"
                    candidate = start_minutes
                    while candidate + service_duration <= end_minutes:
                        candidate_end = candidate + service_duration
                        if d == start_date and candidate <= current_minutes:
                            candidate += 15
                            continue
                        overlaps = any(
                            not (candidate_end <= busy_start or candidate >= busy_end)
                            for busy_start, busy_end in busy_ranges
                        )
                        if not overlaps:
                            status = "available"
                            break
                        candidate += 15

            days.append(
                {
                    "date": d_iso,
                    "day_name": day_name,
                    "day_label": DAY_LABELS[day_name],
                    "display": d.strftime("%d/%m"),
                    "status": status,
                }
            )

        return days
    finally:
        if internal_conn:
            internal_cursor.close()
            internal_conn.close()


def get_owner_store_full(owner_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        cursor.execute(
            """
            SELECT id, name, category, description, location, image_urls, location_lat, location_lng,
                   COALESCE(reminder_minutes_before, 30)
            FROM stores
            WHERE owner_id = %s
            """,
            (owner_id,),
        )
        store_row = cursor.fetchone()
        if not store_row:
            return None

        store = {
            "id": store_row[0],
            "name": store_row[1],
            "category": store_row[2],
            "description": store_row[3],
            "location": store_row[4] or "",
            "image_urls": parse_json_list(store_row[5]),
            "location_lat": store_row[6],
            "location_lng": store_row[7],
            "reminder_minutes_before": store_row[8],
        }

        cursor.execute(
            """
            SELECT id, name, price, duration_minutes
            FROM services
            WHERE store_id = %s
            ORDER BY id
            """,
            (store["id"],),
        )
        services = [
            {"id": s[0], "name": s[1], "price": float(s[2]), "duration": s[3]}
            for s in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT day_of_week, is_open, start_time, end_time
            FROM working_hours
            WHERE store_id = %s
            ORDER BY id
            """,
            (store["id"],),
        )
        working_hours = {
            row[0]: {
                "is_open": row[1],
                "start_time": str(row[2])[:5] if row[2] else "",
                "end_time": str(row[3])[:5] if row[3] else "",
            }
            for row in cursor.fetchall()
        }

        return {"store": store, "services": services, "working_hours": working_hours}
    finally:
        cursor.close()
        conn.close()


def get_owner_day_appointments(store_id, selected_date):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT a.id, a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time, s.name
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            WHERE a.store_id = %s AND a.appointment_date = %s
            ORDER BY a.appointment_time
            """,
            (store_id, selected_date),
        )
        rows = cursor.fetchall()

        return [
            {
                "id": r[0],
                "customer_name": r[1],
                "customer_phone": r[2],
                "date": str(r[3]),
                "time": str(r[4])[:5],
                "service_name": r[5] or "",
            }
            for r in rows
        ]
    finally:
        cursor.close()
        conn.close()


def get_owner_period_appointments(store_id, selected_date, period):
    try:
        anchor = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError:
        anchor = date.today()

    if period == "month":
        start_date = anchor - timedelta(days=30)
        end_date = anchor + timedelta(days=1)
        label = f"{start_date.strftime('%d/%m')} - {anchor.strftime('%d/%m')}"
    elif period == "week":
        start_date = anchor - timedelta(days=7)
        end_date = anchor + timedelta(days=1)
        label = f"{start_date.strftime('%d/%m')} - {anchor.strftime('%d/%m')}"
    else:
        start_date = anchor
        end_date = anchor + timedelta(days=1)
        label = anchor.strftime("%d/%m/%Y")
        period = "day"

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT a.id, a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time, s.name
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            WHERE a.store_id = %s
              AND a.appointment_date >= %s
              AND a.appointment_date < %s
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
            LIMIT 120
            """,
            (store_id, start_date.isoformat(), end_date.isoformat()),
        )
        rows = cursor.fetchall()
        return {
            "period": period,
            "label": label,
            "items": [
                {
                    "id": r[0],
                    "customer_name": r[1],
                    "customer_phone": r[2],
                    "date": str(r[3]),
                    "time": str(r[4])[:5],
                    "service_name": r[5] or "",
                }
                for r in rows
            ],
        }
    finally:
        cursor.close()
        conn.close()


def get_store_ratings_summary(store_id, cursor=None):
    internal_conn = None
    internal_cursor = cursor

    try:
        if internal_cursor is None:
            internal_conn = get_connection()
            internal_cursor = internal_conn.cursor()

        internal_cursor.execute(
            """
            SELECT COALESCE(ROUND(AVG(rating)::numeric, 1), 0), COUNT(*)
            FROM ratings
            WHERE store_id = %s AND status = 'accepted'
            """,
            (store_id,),
        )
        avg_rating, total = internal_cursor.fetchone()

        internal_cursor.execute(
            """
            SELECT customer_name, rating, comment, created_at
            FROM ratings
            WHERE store_id = %s AND status = 'accepted'
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (store_id,),
        )
        rows = internal_cursor.fetchall()

        return {
            "average": float(avg_rating or 0),
            "count": total or 0,
            "items": [
                {
                    "customer_name": r[0],
                    "rating": r[1],
                    "comment": r[2] or "",
                    "created_at": r[3].strftime("%d/%m/%Y") if r[3] else "",
                }
                for r in rows
            ],
        }
    finally:
        if internal_conn:
            internal_cursor.close()
            internal_conn.close()


def get_pending_owner_rating_requests(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT id, customer_name, rating, comment, created_at
            FROM ratings
            WHERE store_id = %s AND status = 'pending'
            ORDER BY created_at DESC
            """,
            (store_id,),
        )
        rows = cursor.fetchall()

        return [
            {
                "id": r[0],
                "customer_name": r[1],
                "rating": r[2],
                "comment": r[3] or "",
                "created_at": r[4].strftime("%d/%m/%Y %H:%M") if r[4] else "",
            }
            for r in rows
        ]
    finally:
        cursor.close()
        conn.close()


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/signup/<role>", methods=["GET", "POST"])
def signup(role):
    if role not in ["customer", "owner"]:
        return "Invalid role", 400

    if request.method == "POST":
        full_name = clean_person_name(request.form["full_name"])
        email = normalize_email(request.form["email"])
        password = request.form["password"].strip()

        if not full_name:
            flash("יש להזין שם מלא אמיתי ותקין.")
            return redirect(url_for("signup", role=role))

        if not is_valid_email(email):
            flash("יש להזין כתובת אימייל אמיתית ותקינה.")
            return redirect(url_for("signup", role=role))

        if len(password) < 8:
            flash("הסיסמה חייבת להכיל לפחות 8 תווים.")
            return redirect(url_for("signup", role=role))

        password_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing_user = cursor.fetchone()

            if existing_user:
                flash("האימייל כבר קיים במערכת.")
                return redirect(url_for("signup", role=role))

            cursor.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
                """,
                (full_name, email, password_hash, role),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        flash("החשבון נוצר בהצלחה. אפשר להתחבר עכשיו.")
        return redirect(url_for("login"))

    return render_template("signup.html", role=role)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = normalize_email(request.form["email"])
        password = request.form["password"].strip()

        if not is_valid_email(email):
            flash("יש להזין כתובת אימייל תקינה.")
            return redirect(url_for("login"))

        conn = get_connection()
        cursor = conn.cursor()

        try:
            ensure_owner_session_schema(cursor)
            cursor.execute(
                """
                SELECT id, full_name, email, password_hash, role,
                       active_owner_session_token, active_owner_session_seen_at
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            user = cursor.fetchone()

            if user and check_password_hash(user[3], password):
                remember_login = request.form.get("remember_login") == "on"
                session.permanent = remember_login
                owner_session_token = None
                login_time = now_local()

                if user[4] == "owner":
                    active_token = user[5]
                    active_seen_at = user[6]
                    active_is_recent = active_seen_at and active_seen_at >= login_time - OWNER_SESSION_TIMEOUT

                    if active_token and active_is_recent:
                        flash("Oops, your business account is already open on another device. To open it here, log out from the other device first.")
                        return redirect(url_for("login"))

                    owner_session_token = secrets.token_urlsafe(32)
                    ensure_owner_session_schema(cursor)
                    cursor.execute(
                        """
                        UPDATE users
                        SET active_owner_session_token = %s,
                            active_owner_session_seen_at = %s
                        WHERE id = %s
                        """,
                        (owner_session_token, login_time, user[0]),
                    )
                    conn.commit()

                session["user_id"] = user[0]
                session["full_name"] = user[1]
                session["email"] = user[2]
                session["role"] = user[4]
                session["last_activity_at"] = login_time.isoformat()
                if owner_session_token:
                    session["owner_session_token"] = owner_session_token
                    session["owner_session_last_touch"] = login_time.isoformat()
                return redirect(url_for("work" if user[4] == "owner" else "pick"))
        finally:
            cursor.close()
            conn.close()

        flash("האימייל או הסיסמה אינם נכונים.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))

        if not is_valid_email(email):
            flash("Please enter a valid email address.")
            return redirect(url_for("forgot_password"))

        conn = get_connection()
        cursor = conn.cursor()
        reset_sent = False

        try:
            ensure_password_reset_schema(cursor)
            cursor.execute(
                "SELECT id, full_name, email FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()

            if user:
                reset_code = f"{secrets.randbelow(1000000):06d}"
                cursor.execute(
                    """
                    UPDATE password_reset_codes
                    SET used_at = %s
                    WHERE user_id = %s AND used_at IS NULL
                    """,
                    (now_local(), user[0]),
                )
                cursor.execute(
                    """
                    INSERT INTO password_reset_codes (user_id, code_hash, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (user[0], generate_password_hash(reset_code), now_local() + timedelta(minutes=15)),
                )
                reset_sent = send_password_reset_email(user[2], user[1], reset_code)

            conn.commit()
        finally:
            cursor.close()
            conn.close()

        if reset_sent:
            flash("We sent a reset code to your email. Enter it below to choose a new password.")
            return redirect(url_for("reset_password", email=email))

        if not email_configured():
            flash("Email is not configured yet, so password reset codes cannot be sent.")
        else:
            flash("If this email exists, a reset code was sent.")
        return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = normalize_email(request.args.get("email") or request.form.get("email", ""))

    if request.method == "POST":
        code = re.sub(r"\D", "", request.form.get("code", ""))
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not is_valid_email(email):
            flash("Please enter a valid email address.")
            return redirect(url_for("reset_password"))

        if len(code) != 6:
            flash("Please enter the 6 digit code from your email.")
            return redirect(url_for("reset_password", email=email))

        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return redirect(url_for("reset_password", email=email))

        if password != confirm_password:
            flash("The passwords do not match.")
            return redirect(url_for("reset_password", email=email))

        conn = get_connection()
        cursor = conn.cursor()

        try:
            ensure_password_reset_schema(cursor)
            cursor.execute(
                "SELECT id FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()

            if not user:
                flash("The reset code is not valid or has expired.")
                return redirect(url_for("reset_password", email=email))

            cursor.execute(
                """
                SELECT id, code_hash, attempts
                FROM password_reset_codes
                WHERE user_id = %s
                  AND used_at IS NULL
                  AND expires_at >= %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user[0], now_local()),
            )
            reset_row = cursor.fetchone()

            if not reset_row or reset_row[2] >= 5 or not check_password_hash(reset_row[1], code):
                if reset_row:
                    cursor.execute(
                        "UPDATE password_reset_codes SET attempts = attempts + 1 WHERE id = %s",
                        (reset_row[0],),
                    )
                    conn.commit()
                flash("The reset code is not valid or has expired.")
                return redirect(url_for("reset_password", email=email))

            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (generate_password_hash(password), user[0]),
            )
            cursor.execute(
                "UPDATE password_reset_codes SET used_at = %s WHERE id = %s",
                (now_local(), reset_row[0]),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

        flash("Your password was updated. You can log in now.")
        return redirect(url_for("login"))

    return render_template("reset_password.html", email=email)


@app.route("/logout")
def logout():
    if session.get("role") == "owner" and session.get("user_id") and session.get("owner_session_token"):
        conn = get_connection()
        cursor = conn.cursor()
        try:
            ensure_owner_session_schema(cursor)
            cursor.execute(
                """
                UPDATE users
                SET active_owner_session_token = NULL,
                    active_owner_session_seen_at = NULL
                WHERE id = %s AND active_owner_session_token = %s
                """,
                (session["user_id"], session["owner_session_token"]),
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    session.clear()
    return redirect(url_for("home"))


@app.route("/account")
def account():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") == "owner":
        return redirect(url_for("work"))
    return render_template("account.html")


@app.route("/work")
def work():
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    data = get_owner_store_full(owner_id)
    selected_date = request.args.get("selected_date") or date.today().isoformat()
    appointment_period = request.args.get("appointment_period") or "day"
    if appointment_period not in {"day", "week", "month"}:
        appointment_period = "day"

    if not data:
        return render_template(
            "work.html",
            store=None,
            services=[],
            working_hours={},
            categories=get_categories(),
            calendar_days=[],
            selected_date=selected_date,
            day_appointments=[],
            period_appointments={"period": appointment_period, "label": selected_date, "items": []},
            pending_ratings=[],
            analytics=empty_owner_analytics(),
            reminder_options=REMINDER_OPTIONS,
        )

    try:
        calendar_days = get_store_calendar_days(data["store"]["id"])
    except Exception:
        calendar_days = []

    try:
        pending_ratings = get_pending_owner_rating_requests(data["store"]["id"])
    except Exception:
        pending_ratings = []

    try:
        day_appointments = get_owner_day_appointments(data["store"]["id"], selected_date)
    except Exception:
        day_appointments = []

    try:
        period_appointments = get_owner_period_appointments(data["store"]["id"], selected_date, appointment_period)
    except Exception:
        period_appointments = {"period": appointment_period, "label": selected_date, "items": []}

    try:
        analytics = get_owner_analytics(data["store"]["id"])
    except Exception:
        analytics = empty_owner_analytics()

    return render_template(
        "work.html",
        store=data["store"],
        services=data["services"],
        working_hours=data["working_hours"],
        categories=get_categories(),
        calendar_days=calendar_days,
        selected_date=selected_date,
        day_appointments=day_appointments,
        period_appointments=period_appointments,
        pending_ratings=pending_ratings,
        analytics=analytics,
        reminder_options=REMINDER_OPTIONS,
    )


@app.route("/add-store", methods=["POST"])
def add_store():
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        cursor.execute("SELECT id FROM stores WHERE owner_id = %s", (owner_id,))
        if cursor.fetchone():
            flash("כל בעל עסק יכול להגדיר עסק אחד בלבד.")
            return redirect(url_for("work"))

        name = clean_text(request.form["name"], min_len=2, max_len=120)
        category = clean_text(normalize_category_name(request.form["category"]), min_len=2, max_len=80)
        description = clean_text(request.form["description"], min_len=10, max_len=500)
        location = clean_optional_text(request.form.get("location"), max_len=255)
        location_lat = clean_optional_coordinate(request.form.get("location_lat"), -90, 90)
        location_lng = clean_optional_coordinate(request.form.get("location_lng"), -180, 180)
        reminder_minutes = clean_reminder_minutes(request.form.get("reminder_minutes_before"))
        try:
            image_urls = build_store_image_list([], request.files.getlist("image_file[]"))
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("work"))

        if not name:
            flash("יש להזין שם עסק תקין.")
            return redirect(url_for("work"))

        if not category:
            flash("יש להזין קטגוריה.")
            return redirect(url_for("work"))

        if not description:
            flash("יש להזין תיאור עסק אמיתי של לפחות 10 תווים.")
            return redirect(url_for("work"))

        if location is None:
            flash("Please enter a valid store location, or leave it empty.")
            return redirect(url_for("work"))

        if (request.form.get("location_lat") and location_lat is None) or (request.form.get("location_lng") and location_lng is None):
            flash("Please choose a valid map location, or clear it.")
            return redirect(url_for("work"))

        ensure_category_exists(category, owner_id, cursor)

        cursor.execute(
            """
            INSERT INTO stores (
                name, category, description, location, image_urls,
                location_lat, location_lng, reminder_minutes_before, owner_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                name,
                category,
                description,
                location,
                json.dumps(image_urls),
                location_lat,
                location_lng,
                reminder_minutes,
                owner_id,
            ),
        )
        store_id = cursor.fetchone()[0]

        service_names = request.form.getlist("service_name[]")
        service_prices = request.form.getlist("service_price[]")
        service_durations = request.form.getlist("service_duration[]")
        valid_services, service_error = validate_service_inputs(service_names, service_prices, service_durations)

        if service_error:
            conn.rollback()
            flash(service_error)
            return redirect(url_for("work"))

        for service_name, service_price, service_duration in valid_services:
            cursor.execute(
                """
                INSERT INTO services (store_id, name, price, duration_minutes)
                VALUES (%s, %s, %s, %s)
                """,
                (store_id, service_name, service_price, service_duration),
            )

        for day in DAYS:
            is_open = request.form.get(f"is_open_{day}") == "true"
            start_time = request.form.get(f"start_time_{day}") or None
            end_time = request.form.get(f"end_time_{day}") or None

            cursor.execute(
                """
                INSERT INTO working_hours (store_id, day_of_week, is_open, start_time, end_time)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (store_id, day, is_open, start_time, end_time),
            )

        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("העסק נוצר בהצלחה.")
    return redirect(url_for("work"))
@app.route("/update-store/<int:store_id>", methods=["POST"])
def update_store(store_id):
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        cursor.execute(
            "SELECT image_urls FROM stores WHERE id = %s AND owner_id = %s",
            (store_id, owner_id)
        )
        store_row = cursor.fetchone()
        if not store_row:
            flash("אין לך הרשאה לערוך את העסק הזה.")
            return redirect(url_for("work"))
        stored_image_urls = parse_json_list(store_row[0])

        name = clean_text(request.form["name"], min_len=2, max_len=120)
        category = clean_text(normalize_category_name(request.form["category"]), min_len=2, max_len=80)
        description = clean_text(request.form["description"], min_len=10, max_len=500)
        location = clean_optional_text(request.form.get("location"), max_len=255)
        location_lat = clean_optional_coordinate(request.form.get("location_lat"), -90, 90)
        location_lng = clean_optional_coordinate(request.form.get("location_lng"), -180, 180)
        reminder_minutes = clean_reminder_minutes(request.form.get("reminder_minutes_before"))
        try:
            image_urls = build_store_image_list(
                request.form.getlist("existing_image_url[]"),
                request.files.getlist("image_file[]"),
                stored_image_urls,
            )
        except ValueError as exc:
            flash(str(exc))
            return redirect(url_for("work"))

        if not name:
            flash("יש להזין שם עסק תקין.")
            return redirect(url_for("work"))

        if not category:
            flash("יש להזין קטגוריה.")
            return redirect(url_for("work"))

        if not description:
            flash("יש להזין תיאור עסק אמיתי של לפחות 10 תווים.")
            return redirect(url_for("work"))

        ensure_category_exists(category, owner_id, cursor)

        if location is None:
            flash("Please enter a valid store location, or leave it empty.")
            return redirect(url_for("work"))

        if (request.form.get("location_lat") and location_lat is None) or (request.form.get("location_lng") and location_lng is None):
            flash("Please choose a valid map location, or clear it.")
            return redirect(url_for("work"))

        cursor.execute(
            """
            UPDATE stores
            SET name = %s, category = %s, description = %s, location = %s,
                image_urls = %s, location_lat = %s, location_lng = %s,
                reminder_minutes_before = %s
            WHERE id = %s AND owner_id = %s
            """,
            (
                name,
                category,
                description,
                location,
                json.dumps(image_urls),
                location_lat,
                location_lng,
                reminder_minutes,
                store_id,
                owner_id,
            ),
        )

        # update ONLY working hours
        cursor.execute("DELETE FROM working_hours WHERE store_id = %s", (store_id,))

        for day in DAYS:
            is_open = request.form.get(f"is_open_{day}") == "true"
            start_time = request.form.get(f"start_time_{day}") or None
            end_time = request.form.get(f"end_time_{day}") or None

            cursor.execute(
                """
                INSERT INTO working_hours (store_id, day_of_week, is_open, start_time, end_time)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (store_id, day, is_open, start_time, end_time),
            )

        conn.commit()
        flash("העסק ושעות העבודה עודכנו בהצלחה.")

    except Exception as e:
        conn.rollback()
        print("UPDATE STORE ERROR:", e)
        flash(f"שגיאה בעדכון העסק: {e}")

    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("work"))
@app.route("/delete-store/<int:store_id>", methods=["POST"])
def delete_store(store_id):
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM stores WHERE id = %s AND owner_id = %s", (store_id, owner_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("העסק נמחק.")
    return redirect(url_for("work"))


@app.route("/pick")
def pick():
    search = request.args.get("search", "").strip()
    category = normalize_category_name(request.args.get("category", ""))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        params = []
        conditions = []

        if search:
            conditions.append("(name ILIKE %s OR category ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if category:
            conditions.append("category = %s")
            params.append(category)

        query = "SELECT id, name, category, description, location, image_urls, location_lat, location_lng FROM stores"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC"

        cursor.execute(query, tuple(params))
        stores = [
            {
                "id": row[0],
                "name": row[1],
                "category": row[2],
                "description": row[3],
                "location": row[4] or "",
                "image_urls": parse_json_list(row[5]),
                "location_lat": row[6],
                "location_lng": row[7],
                "public_url": store_details_url(row[0], row[1]),
            }
            for row in cursor.fetchall()
        ]

        categories = get_categories(cursor)
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "pick.html",
        stores=stores,
        search=search,
        selected_category=category,
        categories=categories,
    )


def empty_owner_analytics():
    return {
        "month_label": now_local().strftime("%B %Y"),
        "month_revenue": 0,
        "month_appointments": 0,
        "month_customers": 0,
        "today_appointments": 0,
        "upcoming_appointments": 0,
        "all_time_appointments": 0,
        "top_services": [],
        "recent_appointments": [],
        "daily_revenue": [],
    }


def get_owner_analytics(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    current = now_local()
    month_start = date(current.year, current.month, 1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    today_start = current.date()
    tomorrow = today_start + timedelta(days=1)

    analytics = empty_owner_analytics()

    try:
        cursor.execute(
            """
            SELECT COUNT(*),
                   COUNT(DISTINCT customer_id),
                   COALESCE(SUM(COALESCE(s.price, 0)), 0)
            FROM appointments a
            LEFT JOIN services s ON s.id = a.service_id
            WHERE a.store_id = %s
              AND a.appointment_date >= %s
              AND a.appointment_date < %s
            """,
            (store_id, month_start, next_month),
        )
        month_count, month_customers, month_revenue = cursor.fetchone()

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM appointments
            WHERE store_id = %s
              AND appointment_date >= %s
              AND appointment_date < %s
            """,
            (store_id, today_start, tomorrow),
        )
        today_appointments = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM appointments
            WHERE store_id = %s
              AND (appointment_date + appointment_time) >= %s
            """,
            (store_id, current),
        )
        upcoming_appointments = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM appointments WHERE store_id = %s", (store_id,))
        all_time_appointments = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COALESCE(s.name, 'Unknown service'),
                   COUNT(*) AS booking_count,
                   COALESCE(SUM(COALESCE(s.price, 0)), 0) AS revenue
            FROM appointments a
            LEFT JOIN services s ON s.id = a.service_id
            WHERE a.store_id = %s
              AND a.appointment_date >= %s
              AND a.appointment_date < %s
            GROUP BY s.id, s.name
            ORDER BY booking_count DESC, revenue DESC
            LIMIT 5
            """,
            (store_id, month_start, next_month),
        )
        top_services = [
            {"name": row[0], "bookings": row[1], "revenue": float(row[2] or 0)}
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT a.customer_name, a.customer_phone, a.appointment_date,
                   a.appointment_time, COALESCE(s.name, ''), COALESCE(s.price, 0)
            FROM appointments a
            LEFT JOIN services s ON s.id = a.service_id
            WHERE a.store_id = %s
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
            LIMIT 8
            """,
            (store_id,),
        )
        recent_appointments = [
            {
                "customer_name": row[0],
                "customer_phone": row[1],
                "date": str(row[2]),
                "time": str(row[3])[:5],
                "service_name": row[4],
                "price": float(row[5] or 0),
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT a.appointment_date, COUNT(*), COALESCE(SUM(COALESCE(s.price, 0)), 0)
            FROM appointments a
            LEFT JOIN services s ON s.id = a.service_id
            WHERE a.store_id = %s
              AND a.appointment_date >= %s
              AND a.appointment_date < %s
            GROUP BY a.appointment_date
            ORDER BY a.appointment_date
            """,
            (store_id, month_start, next_month),
        )
        daily_revenue = [
            {"date": str(row[0]), "bookings": row[1], "revenue": float(row[2] or 0)}
            for row in cursor.fetchall()
        ]

        analytics.update(
            {
                "month_revenue": float(month_revenue or 0),
                "month_appointments": month_count or 0,
                "month_customers": month_customers or 0,
                "today_appointments": today_appointments or 0,
                "upcoming_appointments": upcoming_appointments or 0,
                "all_time_appointments": all_time_appointments or 0,
                "top_services": top_services,
                "recent_appointments": recent_appointments,
                "daily_revenue": daily_revenue,
            }
        )
        return analytics
    finally:
        cursor.close()
        conn.close()

@app.route("/business/<path:store_slug>")
def store_details_by_slug(store_slug):
    normalized_slug = (store_slug or "").strip("/")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, name FROM stores ORDER BY id DESC")
        for store_id, store_name in cursor.fetchall():
            if slugify_store_name(store_name) == normalized_slug:
                return store_details(store_id)
    finally:
        cursor.close()
        conn.close()

    return "Store not found", 404


@app.route("/store/<int:store_id>")
def store_details(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        ensure_store_optional_schema(cursor)
        cursor.execute(
            """
            SELECT id, name, category, description, owner_id, location, image_urls, location_lat, location_lng
            FROM stores
            WHERE id = %s
            """,
            (store_id,),
        )
        row = cursor.fetchone()

        if not row:
            return "Store not found", 404

        store = {
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "description": row[3],
            "owner_id": row[4],
            "location": row[5] or "",
            "image_urls": parse_json_list(row[6]),
            "location_lat": row[7],
            "location_lng": row[8],
        }

        cursor.execute(
            "SELECT id, name, price, duration_minutes FROM services WHERE store_id = %s ORDER BY id",
            (store_id,),
        )
        services = [
            {"id": s[0], "name": s[1], "price": float(s[2]), "duration": s[3]}
            for s in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT day_of_week, is_open, start_time, end_time
            FROM working_hours
            WHERE store_id = %s
            ORDER BY id
            """,
            (store_id,),
        )
        working_hours_rows = cursor.fetchall()
        working_hours = [
            {
                "day_of_week": row[0],
                "day_label": DAY_LABELS.get(row[0], row[0]),
                "is_open": row[1],
                "start_time": str(row[2])[:5] if row[2] else "",
                "end_time": str(row[3])[:5] if row[3] else "",
            }
            for row in working_hours_rows
        ]

        appointments = []
        is_owner_view = False
        calendar_days = []
        ratings_summary = {"average": 0, "count": 0, "items": []}

        if "user_id" in session and session.get("role") == "owner" and session["user_id"] == store["owner_id"]:
            is_owner_view = True
            cursor.execute(
                """
                SELECT a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time, s.name
                FROM appointments a
                LEFT JOIN services s ON a.service_id = s.id
                WHERE a.store_id = %s
                ORDER BY a.appointment_date, a.appointment_time
                """,
                (store_id,),
            )
            appointments = [
                {
                    "customer_name": a[0],
                    "customer_phone": a[1],
                    "date": str(a[2]),
                    "time": str(a[3])[:5],
                    "service_name": a[4] or "",
                }
                for a in cursor.fetchall()
            ]

        calendar_days = get_store_calendar_days(store_id, cursor)
        ratings_summary = get_store_ratings_summary(store_id, cursor)
    finally:
        cursor.close()
        conn.close()

    min_date, max_date = today_range()

    return render_template(
        "store_details.html",
        store=store,
        public_store_url=store_details_url(store_id, store["name"], external=True),
        services=services,
        appointments=appointments,
        is_owner_view=is_owner_view,
        min_date=min_date,
        max_date=max_date,
        working_hours=working_hours,
        calendar_days=calendar_days,
        ratings_summary=ratings_summary,
    )


@app.route("/available-slots/<int:store_id>")
def available_slots(store_id):
    service_id = request.args.get("service_id")
    appointment_date = request.args.get("appointment_date")

    if not service_id or not appointment_date:
        return jsonify({"slots": []})

    try:
        slots = cached_available_slots(store_id, service_id, appointment_date)
    except Exception:
        slots = []

    return jsonify({"slots": slots})


@app.route("/book/<int:store_id>", methods=["POST"])
def book(store_id):
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    customer_name = session.get("full_name")
    customer_phone = normalize_phone(request.form["customer_phone"])
    appointment_date = request.form["appointment_date"]
    appointment_time = request.form["appointment_time"]
    service_id = request.form["service_id"]
    customer_id = session["user_id"]

    if not is_valid_phone(customer_phone):
        flash("יש להזין מספר טלפון אמיתי ותקין.")
        return redirect(url_for("store_details", store_id=store_id))

    min_date, max_date = today_range()
    if not (min_date <= appointment_date <= max_date):
        flash("אפשר לקבוע תור רק מהיום ועד 7 ימים קדימה.")
        return redirect(url_for("store_details", store_id=store_id))

    try:
        selected_dt = datetime.strptime(f"{appointment_date} {appointment_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        flash("בחר תאריך ושעה תקינים.")
        return redirect(url_for("store_details", store_id=store_id))

    if selected_dt <= now_local():
        flash("אי אפשר לקבוע תור בזמן שכבר עבר.")
        return redirect(url_for("store_details", store_id=store_id))

    valid_slots = generate_available_slots(store_id, service_id, appointment_date)
    if appointment_time not in valid_slots:
        flash("בחר שעה תקינה מתוך השעות הזמינות בלבד.")
        return redirect(url_for("store_details", store_id=store_id))

    conn = get_connection()
    cursor = conn.cursor()
    appointment_email = None

    try:
        ensure_email_schema(cursor)
        cursor.execute(
            """
            INSERT INTO appointments (
                store_id, service_id, customer_id, customer_name,
                customer_phone, appointment_date, appointment_time
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (store_id, service_id, customer_id, customer_name, customer_phone, appointment_date, appointment_time),
        )
        appointment_id = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT a.id, a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time,
                   cu.email, st.name, sv.name, ou.email
            FROM appointments a
            JOIN users cu ON cu.id = a.customer_id
            JOIN stores st ON st.id = a.store_id
            JOIN users ou ON ou.id = st.owner_id
            LEFT JOIN services sv ON sv.id = a.service_id
            WHERE a.id = %s
            """,
            (appointment_id,),
        )
        row = cursor.fetchone()
        appointment_email = {
            "id": row[0],
            "customer_name": row[1],
            "customer_phone": row[2],
            "date": str(row[3]),
            "time": str(row[4])[:5],
            "customer_email": row[5],
            "store_name": row[6],
            "service_name": row[7] or "",
            "owner_email": row[8],
            "store_url": store_details_url(store_id, row[6], external=True),
        }
        conn.commit()
        clear_available_slots_cache(store_id)
    finally:
        cursor.close()
        conn.close()

    if appointment_email:
        customer_sent, owner_sent = send_booking_emails(appointment_email)
        if customer_sent or owner_sent:
            flash("התור נקבע בהצלחה. שלחנו עדכון במייל.")
        else:
            flash("התור נקבע בהצלחה.")
    else:
        flash("התור נקבע בהצלחה.")

    return redirect(url_for("store_details", store_id=store_id))


@app.route("/tasks/send-reminders", methods=["GET", "POST"])
def send_reminders():
    reminder_secret = os.environ.get("REMINDER_SECRET")
    provided_secret = request.headers.get("X-Reminder-Secret") or request.args.get("secret")

    if not reminder_secret or provided_secret != reminder_secret:
        return jsonify({"error": "unauthorized"}), 401

    result = send_due_reminder_emails()
    window_start = result.get("window_start", now_local())
    window_end = result.get("window_end", window_start + timedelta(minutes=10))

    return jsonify(
        {
            "sent": result.get("sent", 0),
            "email_configured": result.get("email_configured", email_configured()),
            "window_start": window_start.isoformat(timespec="minutes"),
            "window_end": window_end.isoformat(timespec="minutes"),
        }
    )


@app.route("/request-rating/<int:appointment_id>", methods=["POST"])
def request_rating(appointment_id):
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    try:
        rating = int(request.form["rating"])
    except (KeyError, ValueError):
        flash("דירוג לא תקין.")
        return redirect(request.referrer or url_for("pick"))

    comment = request.form.get("comment", "").strip()

    if rating < 1 or rating > 5:
        flash("הדירוג חייב להיות בין 1 ל-5.")
        return redirect(request.referrer or url_for("pick"))

    conn = get_connection()
    cursor = conn.cursor()
    store_id_for_redirect = None

    try:
        cursor.execute(
            """
            SELECT a.id, a.store_id, a.customer_id, a.customer_name,
                   a.appointment_date, a.appointment_time
            FROM appointments a
            WHERE a.id = %s
            """,
            (appointment_id,),
        )
        row = cursor.fetchone()

        if not row:
            flash("התור לא נמצא.")
            return redirect(request.referrer or url_for("pick"))

        store_id_for_redirect = row[1]

        if row[2] != session["user_id"]:
            flash("אין לך הרשאה לשלוח דירוג על התור הזה.")
            return redirect(request.referrer or url_for("pick"))

        appointment_dt = datetime.combine(row[4], row[5])
        if datetime.now() < appointment_dt:
            flash("אפשר לדרג רק אחרי שהתור הסתיים.")
            return redirect(request.referrer or url_for("pick"))

        cursor.execute(
            """
            INSERT INTO ratings (
                appointment_id, store_id, customer_id, customer_name, rating, comment, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (appointment_id)
            DO UPDATE SET
                rating = EXCLUDED.rating,
                comment = EXCLUDED.comment,
                status = 'pending',
                customer_name = EXCLUDED.customer_name
            """,
            (row[0], row[1], session["user_id"], session.get("full_name"), rating, comment),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("בקשת הדירוג נשלחה לבעל העסק לאישור.")
    return redirect(request.referrer or url_for("store_details", store_id=store_id_for_redirect))

@app.route("/add-rating-from-pick", methods=["POST"])
def add_rating_from_pick():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    try:
        store_id = int(request.form["store_id"])
        rating = int(request.form["rating"])
    except (KeyError, ValueError):
        flash("דירוג לא תקין.")
        return redirect(url_for("pick"))

    comment = request.form.get("comment", "").strip()

    if rating < 1 or rating > 5:
        flash("הדירוג חייב להיות בין 1 ל-5.")
        return redirect(url_for("pick"))

    conn = get_connection()
    cursor = conn.cursor()
    redirect_store_id = store_id

    try:
        ensure_rating_schema(cursor)
        cursor.execute(
            """
            INSERT INTO ratings (
                appointment_id, store_id, customer_id, customer_name, rating, comment, status
            )
            VALUES (NULL, %s, %s, %s, %s, %s, 'pending')
            """,
            (
                store_id,
                session["user_id"],
                session.get("full_name"),
                rating,
                comment,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("בקשת הדירוג נשלחה לבעל העסק לאישור.")
    return redirect(url_for("store_details", store_id=redirect_store_id))
@app.route("/owner/rating/<int:rating_id>/<action>", methods=["POST"])
def owner_rating_action(rating_id, action):
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    if action not in ["accept", "decline"]:
        return redirect(url_for("work"))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT r.id
            FROM ratings r
            JOIN stores s ON s.id = r.store_id
            WHERE r.id = %s AND s.owner_id = %s
            """,
            (rating_id, session["user_id"]),
        )
        if not cursor.fetchone():
            flash("הדירוג לא נמצא או שאין הרשאה.")
            return redirect(url_for("work"))

        new_status = "accepted" if action == "accept" else "declined"
        cursor.execute("UPDATE ratings SET status = %s WHERE id = %s", (new_status, rating_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("הבקשה עודכנה.")
    return redirect(url_for("work"))


@app.route("/appointments")
@app.route("/my-bookings")
def my_bookings():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    selected_category = normalize_category_name(request.args.get("category", ""))
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT DISTINCT st.category
            FROM appointments a
            JOIN stores st ON st.id = a.store_id
            WHERE a.customer_id = %s
              AND st.category IS NOT NULL
            ORDER BY st.category
            """,
            (session["user_id"],),
        )
        booking_categories = [row[0] for row in cursor.fetchall()]

        params = [session["user_id"]]
        category_filter = ""
        if selected_category:
            category_filter = " AND st.category = %s"
            params.append(selected_category)

        cursor.execute(
            f"""
            SELECT a.id, st.name, sv.name, a.appointment_date, a.appointment_time,
                   COALESCE(r.status, 'not_sent'), st.category, st.id
            FROM appointments a
            JOIN stores st ON st.id = a.store_id
            LEFT JOIN services sv ON sv.id = a.service_id
            LEFT JOIN ratings r ON r.appointment_id = a.id
            WHERE a.customer_id = %s
            {category_filter}
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
            """,
            tuple(params),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    appointments = [
        {
            "appointment_id": r[0],
            "store_name": r[1],
            "service_name": r[2] or "",
            "date": str(r[3]),
            "time": str(r[4])[:5],
            "rating_status": r[5],
            "store_category": r[6] or "",
            "store_url": store_details_url(r[7], r[1]),
        }
        for r in rows
    ]

    return render_template(
        "appointments.html",
        appointments=appointments,
        booking_categories=booking_categories,
        selected_category=selected_category,
    )


if __name__ == "__main__":
    app.run(debug=True)
