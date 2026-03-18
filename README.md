# 📅 Appointment Booking Web App

A full-stack web application for managing stores and booking appointments.

Built with Flask, PostgreSQL (Neon), and deployed on Render.

---

## 🚀 Live Demo

Production URL: `https://appointment-app-jcyq.onrender.com`

---

## ✨ Features

### 👤 User Side
- View all available stores
- Search stores by name or category
- View store details
- Book appointments
- See existing appointments for each store

### 🏪 Store Owner
- Add a new store
- Define:
  - Name
  - Category
  - Description
  - Advantages
  - Price
  - Duration

### 📆 Booking System
- Prevents duplicate bookings for the same store, date, and time
- Limits booking dates from today up to 7 days ahead
- Displays booked appointments clearly

---

## 🏗️ Tech Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL (Neon)
- **Frontend:** HTML, CSS
- **Deployment:** Render
- **Production Server:** Gunicorn
- **Version Control:** Git + GitHub

---

## 📁 Project Structure

```text
appointment-app/
│
├── app.py
├── requirements.txt
├── startup.txt
├── README.md
├── .gitignore
│
├── static/
│   ├── css/
│   ├── js/
│   └── style.css
│
└── templates/
    ├── index.html
    ├── work.html
    ├── pick.html
    ├── store_details.html
    └── appointments.html
⚙️ Local Setup
1. Clone the repo
git clone https://github.com/hady1012/appointment-app.git
cd appointment-app
2. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate
3. Install dependencies
pip install -r requirements.txt
4. Create environment variable

Create a .env file locally:

DATABASE_URL=your_neon_connection_string

Example:

DATABASE_URL=postgresql://username:password@host/database?sslmode=require
5. Run the app
python app.py

Then open:

http://127.0.0.1:5000
🧠 Database Setup

Run this in Neon SQL Editor:

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(255) NOT NULL,
    description TEXT,
    advantages TEXT,
    price NUMERIC(10,2) NOT NULL,
    duration_minutes INT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    store_id INT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(50) NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL
);

Optional sample data:

INSERT INTO stores (name, category, description, advantages, price, duration_minutes)
VALUES (
    'Demo Barber',
    'Barbershop',
    'Professional haircut and beard service',
    'Fast service, clean place, friendly staff',
    80.00,
    45
);
🌍 Deployment

This project is deployed on Render and uses Neon PostgreSQL as the cloud database.

Render

Build Command

pip install -r requirements.txt

Start Command

gunicorn app:app
Environment Variable on Render
DATABASE_URL=your_neon_connection_string
🔐 Security Notes

Secrets are stored in environment variables, not in code

.env is excluded through .gitignore

Parameterized SQL queries are used to reduce SQL injection risk

Public repos should never contain database passwords, tokens, or API keys

🎯 Future Improvements

User authentication

Store owner dashboard

Better mobile UI

Automatic available time slots

Booking cancellation/editing

Admin panel

Payment integration

👨‍💻 Author

Hady Amasha
Software Engineering Student