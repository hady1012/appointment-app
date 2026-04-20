# 📅 Appointment Booking Web App

A full-stack web application that helps customers discover businesses and book appointments easily, while allowing business owners to manage their stores, services, working hours, appointments, and customer rating requests.

---

## 🚀 Live Demo

🌍 `https://appointment-app-2-1k3v.onrender.com/`

---

## ✨ Features

### 👤 Customer Features

- Browse all available businesses
- Search by business name
- Filter businesses by category
- View full business details
- View services, prices, and durations
- Book appointments using only available time slots
- View working days and working hours of each business
- View personal bookings
- Send a rating request only after the appointment ends
- Add:
  - ⭐ star rating
  - 💬 optional comment

---

### 🏪 Business Owner Features

- Register and log in as a business owner
- Create one business profile
- Add and manage multiple services
- Set weekly working hours
- Update business information safely
- Type a new business category or reuse an existing one
- View appointments by selected day
- View pending customer rating requests
- See pending rating details before approval:
  - customer name
  - request date
  - rating stars
  - comment
- Accept or decline rating requests before they appear publicly

---

### 📆 Smart Booking System

- Generates available slots dynamically based on:
  - business working hours
  - service duration
  - existing appointments
- Prevents:
  - double booking
  - overlapping appointments
  - invalid manual time selection
- Limits booking range from today up to 7 days ahead
- Marks days visually by availability:
  - green = available
  - red = full / no available slot

---

### ⭐ Rating System

- Customers can request a rating only after the appointment time has passed
- Rating values are from 1 to 5
- Customers can add an optional text comment
- Rating request is first saved as `pending`
- Business owner sees the full pending request before approval
- Owner can:
  - accept the rating
  - decline the rating
- Only accepted ratings appear on the public business page
- Rating is limited to one review per appointment using `appointment_id`

---

## 🏗️ Tech Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL
- **Frontend:** HTML, CSS, Bootstrap
- **Deployment:** Render
- **Server:** Gunicorn
- **Version Control:** Git + GitHub

---

## 📁 Project Structure

```bash
appointment-booking/
│
├── app.py
├── requirements.txt
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
    └── appointments.html
✅ Main Implemented Features
1. Dynamic Business Categories
Business owners can type any category they want
If the category is new, it is saved into the database
Future business owners can reuse the same category
Implemented using:
business_categories table
backend helper ensure_category_exists()
<input list="categories-list"> in work.html
2. Category Filter Near Search Bar
Added category filter beside the search field on the businesses page
Users can search by text and filter by category at the same time
Implemented in:
pick.html
/pick route in app.py
3. Rating Flow with Owner Approval
Customers can request a rating after the service ends
Each request includes:
star rating
optional comment
The rating is saved with status pending
The business owner sees:
customer name
request date
rating stars
comment
The owner can approve or decline the request
Only approved ratings are shown publicly
Implemented using:
ratings table
/request-rating/<appointment_id>
/add-rating-from-pick
/owner/rating/<rating_id>/<action>
rating sections in store_details.html, work.html, and appointments.html
4. Working Days and Hours on Business Page
Added a section on the business page that shows all weekly working days and hours
Implemented in:
store_details.html
working_hours queries in app.py
5. Owner Day Calendar
Added day-based appointment view for the business owner
The owner can click a day and see all appointments for that specific date
Implemented in:
work.html
helper functions:
get_store_calendar_days()
get_owner_day_appointments()
6. User Calendar with Red/Green Availability
Added visual booking day cards for the next 8 days
Green means at least one appointment slot is available
Red means the day is full or unavailable
Implemented in:
store_details.html
helper get_store_calendar_days()
7. Safe Owner Update Flow
Fixed the owner dashboard update process so owners can update:
business name
category
description
working hours
The update no longer crashes when appointments already exist
This prevents errors caused by deleting services that are already linked to appointments
⚙️ Local Setup
1. Clone the repository
git clone https://github.com/hady1012/appointment-app.git
cd appointment-app
2. Create virtual environment
python -m venv .venv
3. Activate the environment

Windows

.venv\Scripts\activate

Mac / Linux

source .venv/bin/activate
4. Install dependencies
pip install -r requirements.txt
5. Configure environment variables

Create a .env file or set environment variables manually.

DATABASE_URL=your_database_url
FLASK_SECRET_KEY=your_secret_key

Example:

DATABASE_URL=postgresql://user:password@host/database?sslmode=require
FLASK_SECRET_KEY=my_super_secret_key_123
6. Run the application
python app.py

Open in browser:

http://127.0.0.1:5000
🧠 Database Schema
Main tables
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
Additional tables
CREATE TABLE IF NOT EXISTS business_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    created_by_owner_id INT REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ratings (
    id SERIAL PRIMARY KEY,
    appointment_id INT UNIQUE NOT NULL REFERENCES appointments(id) ON DELETE CASCADE,
    store_id INT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    customer_name VARCHAR(255) NOT NULL,
    rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
Helpful indexes
CREATE INDEX IF NOT EXISTS idx_ratings_store_status
ON ratings(store_id, status);

CREATE INDEX IF NOT EXISTS idx_appointments_customer_date
ON appointments(customer_id, appointment_date);

CREATE INDEX IF NOT EXISTS idx_appointments_store_date
ON appointments(store_id, appointment_date);
🌍 Deployment on Render
Build Command
pip install -r requirements.txt
Start Command
gunicorn app:app
Environment Variables
DATABASE_URL=your_database_url
FLASK_SECRET_KEY=your_secret_key
🔐 Security
Passwords are hashed using Werkzeug
Sensitive configuration is stored in environment variables
.env should be excluded using .gitignore
SQL injection is reduced using parameterized queries
Booking logic is validated on the backend
Rating requests are verified by user ownership and appointment time
Business owner actions are protected by role checks
🎯 Future Improvements
Cancel appointment
Reschedule appointment
Email or SMS notifications
Business analytics dashboard
Appointment reminders
Customer profile page
Mobile application version
Payment integration
Admin dashboard
Public average rating on business cards
Rating breakdown by stars
👨‍💻 Author

Hady Amasha
Software Engineering Student

⭐ Support

If you like this project:

Star it on GitHub
Use it as a base for your own project
Improve and expand it
💡 Project Level

This project demonstrates:

Full-stack web development
Real-world booking logic
Database design
Dynamic appointment scheduling
Backend validation
Role-based user flows
Clean UI / UX structure
Business-owner approval workflow
Safe store update handling
Review moderation flow