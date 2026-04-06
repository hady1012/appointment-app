import os
from datetime import date, timedelta

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "my_super_secret_key_123"


# ---------------- DATABASE CONNECTION ----------------
def get_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


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
    price = request.form['price'].strip()
    duration_minutes = request.form['duration_minutes'].strip()
    owner_id = session['user_id']

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO stores (name, category, description, advantages, price, duration_minutes, owner_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (name, category, description, advantages, price, duration_minutes, owner_id))

    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for('pick'))


# ---------------- PICK / LIST STORES ----------------
@app.route('/pick')
def pick():
    search = request.args.get('search', '').strip()

    conn = get_connection()
    cursor = conn.cursor()

    if search:
        cursor.execute("""
            SELECT id, name, category, description, advantages, price, duration_minutes
            FROM stores
            WHERE name ILIKE %s OR category ILIKE %s
            ORDER BY id DESC
        """, (f'%{search}%', f'%{search}%'))
    else:
        cursor.execute("""
            SELECT id, name, category, description, advantages, price, duration_minutes
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
            "advantages": row[4],
            "price": float(row[5]),
            "duration_minutes": row[6]
        })

    return render_template('pick.html', stores=stores, search=search)


# ---------------- STORE DETAILS ----------------
@app.route('/store/<int:store_id>')
def store_details(store_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, category, description, advantages, price, duration_minutes
        FROM stores
        WHERE id = %s
    """, (store_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return "Store not found", 404

    cursor.execute("""
        SELECT customer_name, customer_phone, appointment_date, appointment_time
        FROM appointments
        WHERE store_id = %s
        ORDER BY appointment_date, appointment_time
    """, (store_id,))
    appointment_rows = cursor.fetchall()

    cursor.close()
    conn.close()

    store = {
        "id": row[0],
        "name": row[1],
        "category": row[2],
        "description": row[3],
        "advantages": row[4],
        "price": float(row[5]),
        "duration_minutes": row[6]
    }

    appointments = []
    for a in appointment_rows:
        appointments.append({
            "customer_name": a[0],
            "customer_phone": a[1],
            "date": str(a[2]),
            "time": str(a[3])[:5]
        })

    min_date = date.today().isoformat()
    max_date = (date.today() + timedelta(days=7)).isoformat()

    return render_template(
        'store_details.html',
        store=store,
        appointments=appointments,
        min_date=min_date,
        max_date=max_date
    )


# ---------------- BOOK APPOINTMENT ----------------
@app.route('/book/<int:store_id>', methods=['POST'])
def book(store_id):
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))

    customer_name = session.get('full_name')
    customer_phone = request.form['customer_phone'].strip()
    appointment_date = request.form['appointment_date']
    appointment_time = request.form['appointment_time']
    customer_id = session['user_id']

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1
        FROM appointments
        WHERE store_id = %s AND appointment_date = %s AND appointment_time = %s
    """, (store_id, appointment_date, appointment_time))

    exists = cursor.fetchone()

    if exists:
        cursor.close()
        conn.close()
        flash("This time is already booked.")
        return redirect(url_for('store_details', store_id=store_id))

    cursor.execute("""
        INSERT INTO appointments (store_id, customer_id, customer_name, customer_phone, appointment_date, appointment_time)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (store_id, customer_id, customer_name, customer_phone, appointment_date, appointment_time))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Appointment booked successfully.")
    return redirect(url_for('store_details', store_id=store_id))


# ---------------- RUN APP ----------------
if __name__ == '__main__':
    app.run(debug=True)