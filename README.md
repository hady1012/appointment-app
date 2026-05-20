# Golan Pick Appointment Booking Web App

A full-stack Flask web app for booking appointments with local businesses. Customers can discover businesses, choose a service, pick an available time, and request ratings after appointments. Business owners can manage their store, services, working hours, booking reminders, bookings, and rating approvals.

## Live Demo

Production URL:

https://golanpick.com/

WWW URL:

https://www.golanpick.com/

Render fallback URL:

https://appointment-app-2-1k3v.onrender.com/

## Main Features

### Customer

- Browse all businesses
- Search instantly by business name or category
- Filter businesses by category
- View business details, services, prices, and working hours
- See optional store pictures and location when the owner adds them
- Book only real available time slots
- See personal bookings
- Request a rating after an appointment ends

### Business Owner

- Register and log in as an owner
- Create and manage one business profile
- Add an optional store location and up to 5 optional store photos
- Add and update services
- Set weekly working hours
- Choose when customers receive appointment reminder emails
- View appointments by selected day
- See a business analytics pulse with monthly revenue, booking counts, customer counts, popular services, upcoming appointments, and recent customer choices
- Review pending customer rating requests
- Approve or decline ratings before they appear publicly

### Booking System

- Generates slots from business working hours
- Uses service duration when checking availability
- Prevents double booking and overlapping appointments
- Validates the selected slot again on the backend
- Hides past dates and past times, including earlier slots from the current day, and rejects stale bookings on the backend
- Limits bookings from today up to 7 days ahead
- Shows day availability with clear visual states

### Recent UX And Performance Improvements

- Responsive layout for phones, tablets, and desktops
- User-selectable dark mode saved in the browser
- Premium visual styling across the main pages
- Live business filtering while typing
- Faster slot loading on the booking page
- Automatic first service/day selection on booking pages
- Loading states for login, signup, search, and booking
- Reduced database connection overhead for availability checks
- Email notification support for appointment confirmations and reminders
- Password reset by email verification code
- Store ratings can be submitted any time and approved by the owner
- Golan Pick branding with a Golan Heights hero image
- Broken or missing store images fall back gracefully instead of showing broken image boxes

## Tech Stack

- Backend: Flask, Python
- Database: PostgreSQL
- Frontend: HTML, CSS, Bootstrap, vanilla JavaScript
- Deployment: Any Python web host that can run `gunicorn app:app`
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
    golan-heights-hero.jpeg
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
    location TEXT,
    image_urls TEXT,
    advantages TEXT,
    location_lat DOUBLE PRECISION,
    location_lng DOUBLE PRECISION,
    reminder_minutes_before INT DEFAULT 30,
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
    appointment_time TIME,
    reminder_sent_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS business_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    created_by_owner_id INT REFERENCES users(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ratings (
    id SERIAL PRIMARY KEY,
    appointment_id INT UNIQUE REFERENCES appointments(id) ON DELETE CASCADE,
    store_id INT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    customer_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    customer_name VARCHAR(255) NOT NULL,
    rating INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    attempts INT NOT NULL DEFAULT 0,
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

## Public Deployment

This project is not tied to Render. It can be deployed anywhere that supports Python web apps and PostgreSQL.

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app
```

The repository also includes a `Procfile`:

```text
web: gunicorn --bind :$PORT app:app
```

Environment variables:

```bash
DATABASE_URL=your_postgres_connection_string
FLASK_SECRET_KEY=your_secret_key
APP_TIMEZONE=Asia/Jerusalem
BREVO_API_KEY=your_brevo_api_key
BREVO_SENDER_EMAIL=your_verified_sender_email@example.com
BREVO_SENDER_NAME=Appointment Booking
RESEND_API_KEY=your_resend_api_key
MAIL_FROM=onboarding@resend.dev
MAIL_FROM_NAME=Appointment Booking
REMINDER_SECRET=make_a_long_random_secret

# Optional SMTP fallback for paid hosts that allow SMTP traffic:
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your_smtp_username
SMTP_PASSWORD=your_smtp_password
```

Free public hosting options:

- Koyeb free web service + Neon free PostgreSQL: good fit for this Flask app and keeps the app as a normal always-public website within free-tier limits.
- PythonAnywhere free account + hosted PostgreSQL such as Neon: simple for Python apps, but free accounts have platform limitations.
- Vercel/Netlify free static hosting is not enough by itself because this app has login, booking logic, email, and PostgreSQL. It would need a serverless rewrite.
- GitHub Pages is not suitable for the live app because it only hosts static files.

Recommended setup:

- Connect the GitHub repository to the chosen host and enable auto-deploy from the `main` branch.
- Use Neon PostgreSQL with a pooled connection string when possible.
- Keep GoDaddy DNS pointing `A @` to `216.24.57.1` and `CNAME www` to `appointment-app-2-1k3v.onrender.com`.
- Keep `FLASK_SECRET_KEY` private in the host environment variables.
- Use `BREVO_API_KEY` when you want to send to normal customers without Resend test-mode limits.
- Keep email API keys and SMTP passwords private in environment variables.
- Direct Resend API calls include a `User-Agent` header, which Resend requires to avoid 403 error code 1010.
- Free hosting can sleep, throttle traffic, or require periodic redeploys depending on the provider. Paid hosting is usually needed for guaranteed always-on production service.
- On Render, upgrade the web service from Free to Starter when you want the app to stay awake and respond without cold-start delay.

### Restart Or Redeploy

Use this after pushing new code or changing environment variables:

1. Open the web service in your hosting provider.
2. Run a manual deploy or restart.
3. Confirm the latest commit and environment variables are being used.

If the app still uses old files or behaves strangely:

1. Clear the build cache if the provider supports it.
2. Redeploy the latest commit.

Most hosts auto-deploy from GitHub, but a manual deploy is the fastest way to force the latest code and environment variables to run.

### Email Notifications

When `BREVO_API_KEY` is configured, the app sends email through Brevo over HTTPS. If Brevo is not configured, the app tries Resend, then SMTP as a fallback for paid hosts that allow SMTP traffic.

The app sends:

- A confirmation email to the customer after booking.
- A notification email to the business owner after a customer books.
- A reminder email to the customer at the business owner's selected time: 15 minutes, 30 minutes, 1 hour, 2 hours, or 1 day before the appointment.
- A password reset code when a user forgets their password.
- Customer emails include a short Golan Pick thank-you message.

The reminder email needs a scheduled request. The endpoint checks due reminders from the current time through the next 10 minutes, so it still works if the scheduler is a little late. Use your host's scheduler, cron-job.org, GitHub Actions, or any external cron service to call this every 5 minutes:

```bash
curl -X POST "https://golanpick.com/tasks/send-reminders?secret=YOUR_REMINDER_SECRET"
```

Use the same value in the URL that you set for `REMINDER_SECRET`.

## Security Notes

- Passwords are hashed with Werkzeug.
- SQL queries use parameters instead of string interpolation.
- Booking slots are validated on the backend before insert.
- Emails, phone numbers, names, business details, services, prices, and durations are validated on the backend.
- Browser forms also include validation hints for faster user feedback.
- Rating requests are checked against appointment ownership and time.
- Owner-only actions are protected by session role checks.
- Secrets belong in environment variables, not in Git.
- Full ownership proof would require email verification links and SMS verification codes.

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
