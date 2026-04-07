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
