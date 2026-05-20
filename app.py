import os
import base64
import json
import re
import secrets
import smtplib
import ssl
from datetime import date, timedelta, datetime
from email.message import EmailMessage
from urllib.parse import urlparse
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
APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Jerusalem"))
STORE_OPTIONAL_SCHEMA_READY = False
OWNER_SESSION_SCHEMA_READY = False
OWNER_SESSION_TIMEOUT = timedelta(hours=12)
OWNER_SESSION_CHECK_INTERVAL = timedelta(seconds=30)
PERFORMANCE_INDEXES_READY = False
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
        )

    return PooledConnection(DB_POOL.getconn(), DB_POOL)


def now_local():
    return datetime.now(APP_TIMEZONE).replace(tzinfo=None)


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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_services_store_id ON services(store_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_working_hours_store_day ON working_hours(store_id, day_of_week)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_store_date_time ON appointments(store_id, appointment_date, appointment_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_appointments_customer_id ON appointments(customer_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ratings_store_status ON ratings(store_id, status)")
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


def send_password_reset_email(user_email, full_name, reset_code):
    subject = "Your My Marketplace password reset code"
    body = f"""Hi {full_name},

We received a request to reset your My Marketplace password.

Your verification code is: {reset_code}

This code expires in 15 minutes. If you did not ask to reset your password, you can ignore this email.
"""
    return send_email(user_email, subject, body)


@app.before_request
def guard_owner_single_device_session():
    if request.endpoint in {"static", "login", "logout"}:
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


def get_store_calendar_days(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT id, duration_minutes
            FROM services
            WHERE store_id = %s
            ORDER BY duration_minutes ASC, id ASC
            LIMIT 1
            """,
            (store_id,),
        )
        service_row = cursor.fetchone()

        start_date = now_local().date()
        end_date = start_date + timedelta(days=8)
        working_hours_by_day = {}
        appointments_by_date = {}

        if service_row:
            cursor.execute(
                """
                SELECT day_of_week, is_open, start_time, end_time
                FROM working_hours
                WHERE store_id = %s
                """,
                (store_id,),
            )
            working_hours_by_day = {
                row[0]: {"is_open": row[1], "start_time": row[2], "end_time": row[3]}
                for row in cursor.fetchall()
            }

            cursor.execute(
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
            for appointment_date, appointment_time, duration in cursor.fetchall():
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
        cursor.close()
        conn.close()


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


def get_store_ratings_summary(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT COALESCE(ROUND(AVG(rating)::numeric, 1), 0), COUNT(*)
            FROM ratings
            WHERE store_id = %s AND status = 'accepted'
            """,
            (store_id,),
        )
        avg_rating, total = cursor.fetchone()

        cursor.execute(
            """
            SELECT customer_name, rating, comment, created_at
            FROM ratings
            WHERE store_id = %s AND status = 'accepted'
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (store_id,),
        )
        rows = cursor.fetchall()

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
        cursor.close()
        conn.close()


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
        finally:
            cursor.close()
            conn.close()

        if user and check_password_hash(user[3], password):
            owner_session_token = None
            if user[4] == "owner":
                active_token = user[5]
                active_seen_at = user[6]
                active_is_recent = active_seen_at and active_seen_at >= now_local() - OWNER_SESSION_TIMEOUT

                if active_token and active_is_recent:
                    flash("Oops, your business account is already open on another device. To open it here, log out from the other device first.")
                    return redirect(url_for("login"))

                owner_session_token = secrets.token_urlsafe(32)
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    ensure_owner_session_schema(cursor)
                    cursor.execute(
                        """
                        UPDATE users
                        SET active_owner_session_token = %s,
                            active_owner_session_seen_at = %s
                        WHERE id = %s
                        """,
                        (owner_session_token, now_local(), user[0]),
                    )
                    conn.commit()
                finally:
                    cursor.close()
                    conn.close()

            session["user_id"] = user[0]
            session["full_name"] = user[1]
            session["email"] = user[2]
            session["role"] = user[4]
            if owner_session_token:
                session["owner_session_token"] = owner_session_token
                session["owner_session_last_touch"] = now_local().isoformat()
            return redirect(url_for("work" if user[4] == "owner" else "pick"))

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


@app.route("/work")
def work():
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    data = get_owner_store_full(owner_id)
    selected_date = request.args.get("selected_date") or date.today().isoformat()

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
    finally:
        cursor.close()
        conn.close()

    min_date, max_date = today_range()

    try:
        calendar_days = get_store_calendar_days(store_id)
    except Exception:
        calendar_days = []

    try:
        ratings_summary = get_store_ratings_summary(store_id)
    except Exception:
        ratings_summary = {"average": 0, "count": 0, "items": []}

    return render_template(
        "store_details.html",
        store=store,
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
        slots = generate_available_slots(store_id, service_id, appointment_date)
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
            "store_url": url_for("store_details", store_id=store_id, _external=True),
        }
        conn.commit()
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

    conn = get_connection()
    cursor = conn.cursor()
    sent_count = 0

    try:
        ensure_email_schema(cursor)
        ensure_store_optional_schema(cursor)
        current_time = now_local()
        window_start = current_time
        window_end = current_time + timedelta(minutes=10)

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
            LIMIT 50
            """,
            (current_time, window_end),
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
                "store_url": url_for("store_details", store_id=row[9], _external=True),
                "reminder_minutes": row[10],
            }
            subject = f"Reminder: appointment at {appointment['time']} today"
            reminder_label = REMINDER_OPTIONS.get(appointment["reminder_minutes"], "soon")
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
    finally:
        cursor.close()
        conn.close()

    return jsonify(
        {
            "sent": sent_count,
            "email_configured": email_configured(),
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


@app.route("/my-bookings")
def my_bookings():
    if "user_id" not in session or session.get("role") != "customer":
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT a.id, st.name, sv.name, a.appointment_date, a.appointment_time,
                   COALESCE(r.status, 'not_sent')
            FROM appointments a
            JOIN stores st ON st.id = a.store_id
            LEFT JOIN services sv ON sv.id = a.service_id
            LEFT JOIN ratings r ON r.appointment_id = a.id
            WHERE a.customer_id = %s
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
            """,
            (session["user_id"],),
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
        }
        for r in rows
    ]

    return render_template("appointments.html", appointments=appointments)


if __name__ == "__main__":
    app.run(debug=True)
