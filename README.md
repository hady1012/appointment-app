# 📅 Appointment Booking Web App

A full-stack web application that allows users to discover businesses and book appointments easily, while enabling business owners to manage their stores.

Built with Flask, PostgreSQL, and deployed on Render.

---

## 🚀 Live Demo

🌍 https://appointment-app-jcyq.onrender.com

---

## ✨ Features

### 👤 Customer Features

* Browse all available stores
* Search stores by name or category
* View detailed store information
* Book appointments easily
* View existing booked time slots

### 🏪 Business Owner Features

* Register as a store owner
* Add and manage stores
* Define:

  * Store name
  * Category
  * Description
  * Advantages
  * Price
  * Service duration

### 📆 Booking System

* Prevents double bookings (same date & time)
* Limits bookings from today up to 7 days ahead
* Displays booked slots clearly

---

## 🏗️ Tech Stack

* **Backend:** Flask (Python)
* **Database:** PostgreSQL (Neon)
* **Frontend:** HTML, CSS (Bootstrap)
* **Deployment:** Render
* **Server:** Gunicorn
* **Version Control:** Git + GitHub

---

## 📁 Project Structure

```
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
    ├── login.html
    ├── signup.html
    ├── pick.html
    ├── store_details.html
    ├── work.html
```

---

## ⚙️ Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/hady1012/appointment-app.git
cd appointment-app
```

### 2. Create virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file:

```
DATABASE_URL=your_neon_connection_string
```

Example:

```
DATABASE_URL=postgresql://username:password@host/database?sslmode=require
```

### 5. Run the application

```bash
python app.py
```

Open in browser:

```
http://127.0.0.1:5000
```

---

## 🧠 Database Setup

Run the following SQL in Neon:

```sql
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    full_name VARCHAR(255),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(255) NOT NULL,
    description TEXT,
    advantages TEXT,
    price NUMERIC(10,2) NOT NULL,
    duration_minutes INT NOT NULL,
    owner_id INT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    store_id INT REFERENCES stores(id) ON DELETE CASCADE,
    customer_id INT REFERENCES users(id),
    customer_name VARCHAR(255),
    customer_phone VARCHAR(50),
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL
);
```

---

## 🌍 Deployment

### Render Setup

**Build Command**

```bash
pip install -r requirements.txt
```

**Start Command**

```bash
gunicorn app:app
```

### Environment Variable

```
DATABASE_URL=your_neon_connection_string
```

---

## 🔐 Security

* Passwords are hashed using Werkzeug
* Sensitive data stored in environment variables
* `.env` excluded via `.gitignore`
* Parameterized SQL queries used (prevents SQL injection)

---

## 🎯 Future Improvements

* 👤 Customer dashboard ("My bookings")
* 🧑‍💼 Owner dashboard (appointments & stats)
* ❌ Cancel/edit appointments
* 📱 Mobile-friendly UI improvements
* ⏱️ Automatic time slot generation
* 💳 Payment integration
* 🔔 Notifications system

---

## 👨‍💻 Author

**Hady Amasha**
Software Engineering Student

---

⭐ If you like this project, consider giving it a star on GitHub!
