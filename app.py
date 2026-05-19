import os
import json
import re
import smtplib
import ssl
from datetime import date, timedelta, datetime
from email.message import EmailMessage
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "x8sK29!akL#92jF@pQz")
APP_TIMEZONE = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Jerusalem"))

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


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(database_url, connect_timeout=5)


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
            "User-Agent": "appointment-booking-render/1.0",
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
            "User-Agent": "appointment-booking-render/1.0",
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

Thank you for trusting My Marketplace. We wish you the best time in our store.
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
            SELECT id
            FROM services
            WHERE store_id = %s
            ORDER BY duration_minutes ASC, id ASC
            LIMIT 1
            """,
            (store_id,),
        )
        service_row = cursor.fetchone()
        days = []
        for offset in range(8):
            d = now_local().date() + timedelta(days=offset)
            d_iso = d.isoformat()
            day_name = get_day_name_from_date(d_iso)

            if not service_row:
                status = "closed"
            else:
                try:
                    slots = generate_available_slots(store_id, service_row[0], d_iso, cursor=cursor)
                    status = "available" if slots else "busy"
                except Exception:
                    status = "busy"

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
        cursor.execute(
            """
            SELECT id, name, category, description
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
            cursor.execute(
                """
                SELECT id, full_name, email, password_hash, role
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
            session["user_id"] = user[0]
            session["full_name"] = user[1]
            session["email"] = user[2]
            session["role"] = user[4]
            return redirect(url_for("work" if user[4] == "owner" else "pick"))

        flash("האימייל או הסיסמה אינם נכונים.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
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
    )


@app.route("/add-store", methods=["POST"])
def add_store():
    if "user_id" not in session or session.get("role") != "owner":
        return redirect(url_for("login"))

    owner_id = session["user_id"]
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM stores WHERE owner_id = %s", (owner_id,))
        if cursor.fetchone():
            flash("כל בעל עסק יכול להגדיר עסק אחד בלבד.")
            return redirect(url_for("work"))

        name = clean_text(request.form["name"], min_len=2, max_len=120)
        category = clean_text(normalize_category_name(request.form["category"]), min_len=2, max_len=80)
        description = clean_text(request.form["description"], min_len=10, max_len=500)

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

        cursor.execute(
            """
            INSERT INTO stores (name, category, description, owner_id)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (name, category, description, owner_id),
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
        cursor.execute(
            "SELECT id FROM stores WHERE id = %s AND owner_id = %s",
            (store_id, owner_id)
        )
        if not cursor.fetchone():
            flash("אין לך הרשאה לערוך את העסק הזה.")
            return redirect(url_for("work"))

        name = clean_text(request.form["name"], min_len=2, max_len=120)
        category = clean_text(normalize_category_name(request.form["category"]), min_len=2, max_len=80)
        description = clean_text(request.form["description"], min_len=10, max_len=500)

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

        cursor.execute(
            """
            UPDATE stores
            SET name = %s, category = %s, description = %s
            WHERE id = %s AND owner_id = %s
            """,
            (name, category, description, store_id, owner_id),
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
        params = []
        conditions = []

        if search:
            conditions.append("(name ILIKE %s OR category ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if category:
            conditions.append("category = %s")
            params.append(category)

        query = "SELECT id, name, category, description FROM stores"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC"

        cursor.execute(query, tuple(params))
        stores = [
            {"id": row[0], "name": row[1], "category": row[2], "description": row[3]}
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


@app.route("/store/<int:store_id>")
def store_details(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT id, name, category, description, owner_id
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
        window_start = now_local() + timedelta(minutes=25)
        window_end = now_local() + timedelta(minutes=35)

        cursor.execute(
            """
            SELECT a.id, a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time,
                   cu.email, st.name, sv.name, ou.email, st.id
            FROM appointments a
            JOIN users cu ON cu.id = a.customer_id
            JOIN stores st ON st.id = a.store_id
            JOIN users ou ON ou.id = st.owner_id
            LEFT JOIN services sv ON sv.id = a.service_id
            WHERE a.reminder_sent_at IS NULL
              AND (a.appointment_date + a.appointment_time)
                  BETWEEN %s AND %s
            ORDER BY a.appointment_date, a.appointment_time
            LIMIT 50
            """,
            (window_start, window_end),
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
            }
            subject = f"Reminder: appointment at {appointment['time']} today"
            body = build_appointment_email_body(
                appointment,
                "Reminder: your appointment starts in about 30 minutes.",
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

    return jsonify({"sent": sent_count})


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
