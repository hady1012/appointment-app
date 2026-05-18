# Appointment Booking Web App

A full-stack Flask web app for booking appointments with local businesses. Customers can discover businesses, choose a service, pick an available time, and request ratings after appointments. Business owners can manage their store, services, working hours, bookings, and rating approvals.

## Live Demo

Production URL:

https://appointment-app-2-1k3v.onrender.com/

If the site works on mobile data but not on home Wi-Fi, the deployment is fine and the issue is usually local DNS. Restart the router, run `ipconfig /flushdns` on Windows, or switch DNS to `1.1.1.1` / `8.8.8.8`.

## Main Features

### Customer

- Browse all businesses
- Search instantly by business name or category
- Filter businesses by category
- View business details, services, prices, and working hours
- Book only real available time slots
- See personal bookings
- Request a rating after an appointment ends

### Business Owner

- Register and log in as an owner
- Create and manage one business profile
- Add and update services
- Set weekly working hours
- View appointments by selected day
- Review pending customer rating requests
- Approve or decline ratings before they appear publicly

### Booking System

- Generates slots from business working hours
- Uses service duration when checking availability
- Prevents double booking and overlapping appointments
- Validates the selected slot again on the backend
- Limits bookings from today up to 7 days ahead
- Shows day availability with clear visual states

### Recent UX And Performance Improvements

- Responsive layout for phones, tablets, and desktops
- Premium visual styling across the main pages
- Live business filtering while typing
- Faster slot loading on the booking page
- Automatic first service/day selection on booking pages
- Loading states for login, signup, search, and booking
- Reduced database connection overhead for availability checks

## Tech Stack

- Backend: Flask, Python
- Database: PostgreSQL
- Frontend: HTML, CSS, Bootstrap, vanilla JavaScript
- Deployment: Render
- Database hosting: Neon or any PostgreSQL provider
- Server: Gunicorn
- Version control: Git + GitHub

## Project Structure

```text
appointment-booking/
  app.py
  requirements.txt
  README.md
  static/
    css/
      bootstrap.min.css
    js/
      bootstrap.min.js
    style.css
  templates/
    appointments.html
    index.html
    login.html
    pick.html
    signup.html
    store_details.html
    work.html
```

## Local Setup

1. Clone the repository:

```bash
git clone https://github.com/hady1012/appointment-app.git
cd appointment-app
```

2. Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Set environment variables:

```bash
DATABASE_URL=your_postgres_connection_string
FLASK_SECRET_KEY=your_secret_key
```

Example:

```bash
DATABASE_URL=postgresql://user:password@host/database?sslmode=require
FLASK_SECRET_KEY=my_super_secret_key_123
```

5. Run the app:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Database Schema

Main tables:

```sql
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
```

Helpful indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_ratings_store_status
ON ratings(store_id, status);

CREATE INDEX IF NOT EXISTS idx_appointments_customer_date
ON appointments(customer_id, appointment_date);

CREATE INDEX IF NOT EXISTS idx_appointments_store_date
ON appointments(store_id, appointment_date);

CREATE INDEX IF NOT EXISTS idx_users_email
ON users(email);
```

## Deployment On Render

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app
```

Environment variables:

```bash
DATABASE_URL=your_postgres_connection_string
FLASK_SECRET_KEY=your_secret_key
```

Recommended production setup:

- Use Render auto-deploy from the `main` branch.
- Use Neon PostgreSQL with a pooled connection string when possible.
- Keep `FLASK_SECRET_KEY` private in Render environment variables.
- On Render free instances, the first request after inactivity can be slow because the service sleeps. Upgrade the instance for always-on performance.

## Security Notes

- Passwords are hashed with Werkzeug.
- SQL queries use parameters instead of string interpolation.
- Booking slots are validated on the backend before insert.
- Rating requests are checked against appointment ownership and time.
- Owner-only actions are protected by session role checks.
- Secrets belong in environment variables, not in Git.

## Future Improvements

- Cancel and reschedule appointments
- Email or SMS reminders
- Business analytics dashboard
- Customer profile page
- Admin dashboard
- Payment integration
- Public rating breakdown by stars

## Author

Hady Amasha

Software Engineering Student
