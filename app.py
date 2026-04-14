import os
from datetime import date, timedelta, datetime

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "x8sK29!akL#92jF@pQz")

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
    return psycopg2.connect(database_url)


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
    min_date = date.today()
    max_date = min_date + timedelta(days=7)
    return min_date.isoformat(), max_date.isoformat()


def normalize_category_name(raw_value):
    return " ".join((raw_value or "").strip().split())


def get_categories():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM business_categories ORDER BY LOWER(name)")
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []
    finally:
        cursor.close()
        conn.close()


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


def generate_available_slots(store_id, service_id, appointment_date):
    conn = get_connection()
    cursor = conn.cursor()

    try:
        day_name = get_day_name_from_date(appointment_date)

        cursor.execute(
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
        row = cursor.fetchone()
        if not row:
            return []

        service_duration, is_open, start_time, end_time = row

        if not is_open or not start_time or not end_time:
            return []

        service_duration = int(service_duration)
        start_minutes = time_to_minutes(start_time)
        end_minutes = time_to_minutes(end_time)

        cursor.execute(
            """
            SELECT a.appointment_time, s.duration_minutes
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.store_id = %s AND a.appointment_date = %s
            """,
            (store_id, appointment_date),
        )
        existing_rows = cursor.fetchall()

        busy_ranges = []
        for appointment_time, existing_duration in existing_rows:
            existing_start = time_to_minutes(appointment_time)
            existing_end = existing_start + int(existing_duration)
            busy_ranges.append((existing_start, existing_end))

        slots = []
        current = start_minutes

        while current + service_duration <= end_minutes:
            candidate_start = current
            candidate_end = current + service_duration

            overlaps = any(
                not (candidate_end <= busy_start or candidate_start >= busy_end)
                for busy_start, busy_end in busy_ranges
            )

            if not overlaps:
                slots.append(minutes_to_time_string(candidate_start))

            current += 15

        return slots
    finally:
        cursor.close()
        conn.close()


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
    finally:
        cursor.close()
        conn.close()

    days = []
    for offset in range(8):
        d = date.today() + timedelta(days=offset)
        d_iso = d.isoformat()
        day_name = get_day_name_from_date(d_iso)

        if not service_row:
            status = "closed"
        else:
            try:
                slots = generate_available_slots(store_id, service_row[0], d_iso)
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
        full_name = request.form["full_name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"].strip()
        password_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            existing_user = cursor.fetchone()

            if existing_user:
                flash("Email already exists")
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

        flash("Account created successfully. Please login.")
        return redirect(url_for("login"))

    return render_template("signup.html", role=role)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"].strip()

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

        flash("Invalid email or password")
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

        name = request.form["name"].strip()
        category = normalize_category_name(request.form["category"])
        description = request.form["description"].strip()

        if not category:
            flash("יש להזין קטגוריה.")
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

        for i in range(len(service_names)):
            if service_names[i].strip() and service_prices[i].strip() and service_durations[i].strip():
                cursor.execute(
                    """
                    INSERT INTO services (store_id, name, price, duration_minutes)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (store_id, service_names[i].strip(), service_prices[i].strip(), service_durations[i].strip()),
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
        cursor.execute("SELECT id FROM stores WHERE id = %s AND owner_id = %s", (store_id, owner_id))
        if not cursor.fetchone():
            flash("אין לך הרשאה לערוך את העסק הזה.")
            return redirect(url_for("work"))

        name = request.form["name"].strip()
        category = normalize_category_name(request.form["category"])
        description = request.form["description"].strip()

        if not category:
            flash("יש להזין קטגוריה.")
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

        cursor.execute("DELETE FROM services WHERE store_id = %s", (store_id,))
        cursor.execute("DELETE FROM working_hours WHERE store_id = %s", (store_id,))

        service_names = request.form.getlist("service_name[]")
        service_prices = request.form.getlist("service_price[]")
        service_durations = request.form.getlist("service_duration[]")

        for i in range(len(service_names)):
            if service_names[i].strip() and service_prices[i].strip() and service_durations[i].strip():
                cursor.execute(
                    """
                    INSERT INTO services (store_id, name, price, duration_minutes)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (store_id, service_names[i].strip(), service_prices[i].strip(), service_durations[i].strip()),
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

    flash("העסק עודכן בהצלחה.")
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
    finally:
        cursor.close()
        conn.close()

    return render_template(
        "pick.html",
        stores=stores,
        search=search,
        selected_category=category,
        categories=get_categories(),
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
    customer_phone = request.form["customer_phone"].strip()
    appointment_date = request.form["appointment_date"]
    appointment_time = request.form["appointment_time"]
    service_id = request.form["service_id"]
    customer_id = session["user_id"]

    min_date, max_date = today_range()
    if not (min_date <= appointment_date <= max_date):
        flash("אפשר לקבוע תור רק מהיום ועד 7 ימים קדימה.")
        return redirect(url_for("store_details", store_id=store_id))

    valid_slots = generate_available_slots(store_id, service_id, appointment_date)
    if appointment_time not in valid_slots:
        flash("בחר שעה תקינה מתוך השעות הזמינות בלבד.")
        return redirect(url_for("store_details", store_id=store_id))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO appointments (
                store_id, service_id, customer_id, customer_name,
                customer_phone, appointment_date, appointment_time
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (store_id, service_id, customer_id, customer_name, customer_phone, appointment_date, appointment_time),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    flash("Appointment booked successfully.")
    return redirect(url_for("store_details", store_id=store_id))


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
        cursor.execute(
            """
            SELECT a.id, a.appointment_date, a.appointment_time
            FROM appointments a
            WHERE a.customer_id = %s AND a.store_id = %s
            ORDER BY a.appointment_date DESC, a.appointment_time DESC
            LIMIT 1
            """,
            (session["user_id"], store_id),
        )
        appointment = cursor.fetchone()

        if not appointment:
            flash("אפשר לדרג רק אם היה לך תור בעסק הזה.")
            return redirect(url_for("pick"))

        appointment_dt = datetime.combine(appointment[1], appointment[2])
        if datetime.now() < appointment_dt:
            flash("אפשר לדרג רק אחרי שהתור הסתיים.")
            return redirect(url_for("pick"))

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
            (
                appointment[0],
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