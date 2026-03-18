import os
import psycopg2
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)


# ---------------- DATABASE CONNECTION ----------------
def get_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))


# ---------------- HOME ----------------
@app.route('/')
def home():
    return render_template('index.html')


# ---------------- WORK / ADD STORE ----------------
@app.route('/work')
def work():
    return render_template('work.html')


@app.route('/add-store', methods=['POST'])
def add_store():
    name = request.form['name'].strip()
    category = request.form['category'].strip()
    description = request.form['description'].strip()
    advantages = request.form['advantages'].strip()
    price = request.form['price'].strip()
    duration_minutes = request.form['duration_minutes'].strip()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO stores (name, category, description, advantages, price, duration_minutes)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (name, category, description, advantages, price, duration_minutes))

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
    customer_name = request.form['customer_name'].strip()
    customer_phone = request.form['customer_phone'].strip()
    appointment_date = request.form['appointment_date']
    appointment_time = request.form['appointment_time']

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
        return redirect(url_for('store_details', store_id=store_id))

    cursor.execute("""
        INSERT INTO appointments (store_id, customer_name, customer_phone, appointment_date, appointment_time)
        VALUES (%s, %s, %s, %s, %s)
    """, (store_id, customer_name, customer_phone, appointment_date, appointment_time))

    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for('store_details', store_id=store_id))


# ---------------- RUN APP ----------------
if __name__ == '__main__':
    app.run(debug=True)