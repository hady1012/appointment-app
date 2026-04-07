import os
from datetime import date, timedelta, datetime

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "my_super_secret_key_123"


# ---------------- DATABASE CONNECTION ----------------
def get_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


# ---------------- HELPERS ----------------
def get_day_name_from_date(date_str):
    day_index = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    mapping = {
        6: "sunday",
        0: "monday",
        1: "tuesday",
        2: "wednesday",
        3: "thursday",
        4: "friday",
        5: "saturday"
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


def generate_available_slots(store_id, service_id, appointment_date):
    conn = get_connection()
    cursor = conn.cursor()

    # service duration
    cursor.execute("""
        SELECT duration_minutes
        FROM services
        WHERE id = %s AND store_id = %s
    """, (service_id, store_id))
    service_row = cursor.fetchone()

    if not service_row:
        cursor.close()
        conn.close()
        return []

    service_duration = int(service_row[0])

    # working hours for selected day
    day_name = get_day_name_from_date(appointment_date)

    cursor.execute("""
        SELECT is_open, start_time, end_time
        FROM working_hours
        WHERE store_id = %s AND day_of_week = %s
    """, (store_id, day_name))
    working_row = cursor.fetchone()

    if not working_row:
        cursor.close()
        conn.close()
        return []

    is_open, start_time, end_time = working_row

    if not is_open:
        cursor.close()
        conn.close()
        return []

    start_minutes = time_to_minutes(start_time)
    end_minutes = time_to_minutes(end_time)

    # existing bookings on that date
    cursor.execute("""
        SELECT appointment_time, service_id
        FROM appointments
        WHERE store_id = %s AND appointment_date = %s
    """, (store_id, appointment_date))
    existing_rows = cursor.fetchall()

    busy_ranges = []
    for appointment_time, existing_service_id in existing_rows:
        cursor.execute("""
            SELECT duration_minutes
            FROM services
            WHERE id = %s
        """, (existing_service_id,))
        existing_service = cursor.fetchone()
        if not existing_service:
            continue

        existing_duration = int(existing_service[0])
        existing_start = time_to_minutes(appointment_time)
        existing_end = existing_start + existing_duration
        busy_ranges.append((existing_start, existing_end))

    # generate slots every 15 minutes
    slots = []
    current = start_minutes

    while current + service_duration <= end_minutes:
        candidate_start = current
        candidate_end = current + service_duration

        overlaps = False
        for busy_start, busy_end in busy_ranges:
            if not (candidate_end <= busy_start or candidate_start >= busy_end):
                overlaps = True
                break

        if not overlaps:
            slots.append(minutes_to_time_string(candidate_start))

        current += 15

    cursor.close()
    conn.close()
    return slots


# ---------------- HOME ----------------
@app.route('/')
def home():
    return render_template('index.html')


# ---------------- SIGNUP ----------------
@app.route('/signup/<role>', methods=['GET', 'POST'])
def signup(role):
    if role not in ['customer', 'owner']:
        return "Invalid role", 400

    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        email = request.form['email'].strip()
        password = request.form['password'].strip()

        password_hash = generate_password_hash(password)

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        existing_user = cursor.fetchone()

        if existing_user:
            cursor.close()
            conn.close()
            flash("Email already exists")
            return redirect(url_for('signup', role=role))

        cursor.execute("""
            INSERT INTO users (full_name, email, password_hash, role)
            VALUES (%s, %s, %s, %s)
        """, (full_name, email, password_hash, role))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Account created successfully. Please login.")
        return redirect(url_for('login'))

    return render_template('signup.html', role=role)


# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip()
        password = request.form['password'].strip()

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, full_name, email, password_hash, role
            FROM users
            WHERE email = %s
        """, (email,))
        user = cursor.fetchone()

        cursor.close()
        conn.close()

        if user and check_password_hash(user[3], password):
            session['user_id'] = user[0]
            session['full_name'] = user[1]
            session['email'] = user[2]
            session['role'] = user[4]

            if user[4] == 'owner':
                return redirect(url_for('work'))
            else:
                return redirect(url_for('pick'))

        flash("Invalid email or password")
        return redirect(url_for('login'))

    return render_template('login.html')


# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))


# ---------------- WORK / ADD STORE ----------------
@app.route('/work')
def work():
    if 'user_id' not in session or session.get('role') != 'owner':
        return redirect(url_for('login'))

    return render_template('work.html')


@app.route('/add-store', methods=['POST'])
def add_store():
    if 'user_id' not in session or session.get('role') != 'owner':
        return redirect(url_for('login'))

    name = request.form['name'].strip()
    category = request.form['category'].strip()
    description = request.form['description'].strip()
    advantages = request.form['advantages'].strip()
    owner_id = session['user_id']

    service_names = request.form.getlist('service_name[]')
    service_prices = request.form.getlist('service_price[]')
    service_durations = request.form.getlist('service_duration[]')

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO stores (name, category, description, advantages, owner_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (name, category, description, advantages, owner_id))

    store_id = cursor.fetchone()[0]

    for i in range(len(service_names)):
        service_name = service_names[i].strip()
        service_price = service_prices[i].strip()
        service_duration = service_durations[i].strip()

        if service_name and service_price and service_duration:
            cursor.execute("""
                INSERT INTO services (store_id, name, price, duration_minutes)
                VALUES (%s, %s, %s, %s)
            """, (store_id, service_name, service_price, service_duration))

    days = [
        "sunday", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday"
    ]

    for day in days:
        is_open = request.form.get(f'is_open_{day}') == 'true'
        start_time = request.form.get(f'start_time_{day}')
        end_time = request.form.get(f'end_time_{day}')

        cursor.execute("""
            INSERT INTO working_hours (store_id, day_of_week, is_open, start_time, end_time)
            VALUES (%s, %s, %s, %s, %s)
        """, (store_id, day, is_open, start_time, end_time))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Store created successfully.")
    return redirect(url_for('pick'))


# ---------------- PICK / LIST STORES ----------------
@app.route('/pick')
def pick():
    search = request.args.get('search', '').strip()

    conn = get_connection()
    cursor = conn.cursor()

    if search:
        cursor.execute("""
            SELECT id, name, category, description, advantages
            FROM stores
            WHERE name ILIKE %s OR category ILIKE %s
            ORDER BY id DESC
        """, (f'%{search}%', f'%{search}%'))
    else:
        cursor.execute("""
            SELECT id, name, category, description, advantages
            FROM stores
            ORDER BY id DESC
        """)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    stores = []
    for row in rows:
        stores.append({
            "id": row[0],
            "name": row[1],
            "category": row[2],
            "description": row[3],
            "advantages": row[4]
        })

    return render_template('pick.html', stores=stores, search=search)


# ---------------- STORE DETAILS ----------------
@app.route('/store/<int:store_id>')
def store_details(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, category, description, advantages
        FROM stores
        WHERE id = %s
    """, (store_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return "Store not found", 404

    cursor.execute("""
        SELECT id, name, price, duration_minutes
        FROM services
        WHERE store_id = %s
        ORDER BY id
    """, (store_id,))
    services_rows = cursor.fetchall()

    cursor.execute("""
        SELECT a.customer_name, a.customer_phone, a.appointment_date, a.appointment_time, s.name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.store_id = %s
        ORDER BY a.appointment_date, a.appointment_time
    """, (store_id,))
    appointment_rows = cursor.fetchall()

    cursor.close()
    conn.close()

    store = {
        "id": row[0],
        "name": row[1],
        "category": row[2],
        "description": row[3],
        "advantages": row[4]
    }

    services = []
    for s in services_rows:
        services.append({
            "id": s[0],
            "name": s[1],
            "price": float(s[2]),
            "duration": s[3]
        })

    appointments = []
    for a in appointment_rows:
        appointments.append({
            "customer_name": a[0],
            "customer_phone": a[1],
            "date": str(a[2]),
            "time": str(a[3])[:5],
            "service_name": a[4] if a[4] else ""
        })

    min_date = date.today().isoformat()
    max_date = (date.today() + timedelta(days=7)).isoformat()

    return render_template(
        'store_details.html',
        store=store,
        services=services,
        appointments=appointments,
        min_date=min_date,
        max_date=max_date
    )


# ---------------- AVAILABLE SLOTS ----------------
@app.route('/available-slots/<int:store_id>')
def available_slots(store_id):
    service_id = request.args.get('service_id')
    appointment_date = request.args.get('appointment_date')

    if not service_id or not appointment_date:
        return jsonify({"slots": []})

    slots = generate_available_slots(store_id, service_id, appointment_date)
    return jsonify({"slots": slots})


# ---------------- BOOK APPOINTMENT ----------------
@app.route('/book/<int:store_id>', methods=['POST'])
def book(store_id):
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))

    customer_name = session.get('full_name')
    customer_phone = request.form['customer_phone'].strip()
    appointment_date = request.form['appointment_date']
    appointment_time = request.form['appointment_time']
    service_id = request.form['service_id']
    customer_id = session['user_id']

    valid_slots = generate_available_slots(store_id, service_id, appointment_date)

    if appointment_time not in valid_slots:
        flash("בחר שעה תקינה מתוך השעות הזמינות בלבד.")
        return redirect(url_for('store_details', store_id=store_id))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO appointments (
            store_id,
            service_id,
            customer_id,
            customer_name,
            customer_phone,
            appointment_date,
            appointment_time
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        store_id,
        service_id,
        customer_id,
        customer_name,
        customer_phone,
        appointment_date,
        appointment_time
    ))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Appointment booked successfully.")
    return redirect(url_for('store_details', store_id=store_id))


# ---------------- RUN APP ----------------
if __name__ == '__main__':
    app.run(debug=True)