# 📅 Appointment Booking Web App

A full-stack web application that allows users to discover businesses and book appointments easily, while enabling business owners to fully manage their stores, services, and working hours.

Built with Flask, PostgreSQL (Neon), and deployed on Render.

---

## 🚀 Live Demo

🌍 https://appointment-app-jcyq.onrender.com

---

## ✨ Features

### 👤 Customer Features

- Browse all available businesses
- Search by name or category
- View detailed store pages
- View services (price + duration)
- Book appointments easily
- View existing bookings
- Choose from **available time slots only**
- Prevents invalid or overlapping bookings

---

### 🏪 Business Owner Features

- Register as a store owner
- Create and manage stores
- Add multiple services per store:
  - Service name
  - Price
  - Duration
- Define **weekly working hours**:
  - Open / closed days
  - Start and end time per day

---

### 📆 Smart Booking System

- Generates time slots automatically based on:
  - Working hours
  - Service duration
  - Existing bookings
- Prevents:
  - Double booking
  - Overlapping appointments
- Limits booking range (today → 7 days ahead)
- Validates bookings on backend (secure)

---

## 🏗️ Tech Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL (Neon)
- **Frontend:** HTML, CSS, Bootstrap
- **Deployment:** Render
- **Server:** Gunicorn
- **Version Control:** Git + GitHub

---

## 📁 Project Structure
ts.txt
├── startup.txt
├── README.md
├── .gitignore
│
├── static/
│ ├── css/
│ ├── js/
│ └── style.css
│
└── templates/
├── index.html
├── login.html
├── signup.html
├── pick.html
├── store_details.html
├── work.html


---

## ⚙️ Local Setup

### 1️⃣ Clone the repository

```bash
git clone https://github.com/hady1012/appointment-app.git
cd appointment-app
2️⃣ Create virtual environment
python -m venv .venv

Activate:

Windows

.venv\Scripts\activate

Mac/Linux

source .venv/bin/activate
3️⃣ Install dependencies
pip install -r requirements.txt
4️⃣ Configure environment variables

Create .env file:

DATABASE_URL=your_neon_connection_string

Example:

DATABASE_URL=postgresql://user:password@host/database?sslmode=require
5️⃣ Run the app
python app.py

Open:

http://127.0.0.1:5000
🧠 Database Schema

Run in Neon:

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(255),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL
);

CREATE TABLE stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(255) NOT NULL,
    description TEXT,
    advantages TEXT,
    owner_id INT REFERENCES users(id)
);

CREATE TABLE services (
    id SERIAL PRIMARY KEY,
    store_id INT REFERENCES stores(id) ON DELETE CASCADE,
    name VARCHAR(255),
    price NUMERIC(10,2),
    duration_minutes INT
);

CREATE TABLE working_hours (
    id SERIAL PRIMARY KEY,
    store_id INT REFERENCES stores(id) ON DELETE CASCADE,
    day_of_week VARCHAR(20),
    is_open BOOLEAN,
    start_time TIME,
    end_time TIME
);

CREATE TABLE appointments (
    id SERIAL PRIMARY KEY,
    store_id INT REFERENCES stores(id) ON DELETE CASCADE,
    service_id INT REFERENCES services(id),
    customer_id INT REFERENCES users(id),
    customer_name VARCHAR(255),
    customer_phone VARCHAR(50),
    appointment_date DATE,
    appointment_time TIME
);
🌍 Deployment (Render)
Build Command
pip install -r requirements.txt
Start Command
gunicorn app:app
Environment Variables
DATABASE_URL=your_neon_connection_string
🔐 Security
Passwords hashed using Werkzeug
Environment variables for secrets
.env excluded via .gitignore
SQL injection prevented using parameterized queries
Backend validation for booking logic
🎯 Future Improvements
👤 Customer dashboard ("My bookings")
🧑‍💼 Owner dashboard (appointments + analytics)
❌ Cancel / reschedule appointments
📱 Mobile UI (React / React Native)
🔔 Notifications (email / SMS)
💳 Payment integration (Stripe)
📊 Business insights (revenue, bookings)
👨‍💻 Author

Hady Amasha
Software Engineering Student

⭐ Support

If you like this project:

👉 Give it a ⭐ on GitHub
👉 Share it with others
👉 Use it as a base for your own startup 😉

💡 Project Level

This project demonstrates:

Full-stack development
Real-world booking logic
Database design
Backend validation
Clean UI + UX

👉 Ready to evolve into a real SaaS product


---

## 🔥 What changed (important)

I upgraded your README to match your real system:

- ✅ services table
- ✅ working_hours
- ✅ smart slot generation
- ✅ no old `price/duration` in stores
- ✅ production-level explanation

---

If you want next level (🔥):

👉 I can turn this into:
- GitHub **portfolio-level README (with screenshots + badges)**
- or **CV project description (for jobs)**

Just say: `make it portfolio level`