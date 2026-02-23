# Lokly – Local Experiences Booking Platform

A Flask-based web application that lets users discover, book, and host local cultural experiences across India.

---

## Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Database Schema](#database-schema)
- [Features](#features)
  - [User Authentication](#user-authentication)
  - [Home & Search](#home--search)
  - [Category Browsing](#category-browsing)
  - [Experience Detail](#experience-detail)
  - [Booking System](#booking-system)
  - [Enrollment System](#enrollment-system)
  - [User Dashboard](#user-dashboard)
  - [Become a Host](#become-a-host)
  - [Host Dashboard](#host-dashboard)
  - [Host Activity Management](#host-activity-management)
  - [About Page (Blogs & Reels)](#about-page-blogs--reels)
- [Routes Reference](#routes-reference)
- [Project Structure](#project-structure)
- [Setup & Running](#setup--running)

---

## Overview

Lokly connects locals and travellers to authentic hands-on experiences — pottery workshops, street food tours, dance classes, yoga sessions, trekking adventures, and more. Community members can both **book** existing activities and **host** their own.

---

## Tech Stack

| Layer       | Technology                          |
|-------------|-------------------------------------|
| Backend     | Python 3, Flask                     |
| Auth        | Flask-Bcrypt (password hashing)     |
| Database    | MySQL (`mysql-connector-python`)    |
| File Upload | Werkzeug `secure_filename` + UUID   |
| Frontend    | Jinja2 templates, custom CSS/JS     |

---

## Database Schema

Four tables are created automatically on first run:

### `users`
| Column       | Type         | Notes                  |
|--------------|--------------|------------------------|
| id           | INT PK AI    |                        |
| username     | VARCHAR(120) | unique                 |
| email        | VARCHAR(200) | unique                 |
| password     | VARCHAR(200) | bcrypt hash            |
| created_at   | TIMESTAMP    | default now            |

### `host_activity`
| Column          | Type          | Notes                          |
|-----------------|---------------|--------------------------------|
| id              | INT PK AI     |                                |
| host_user_id    | INT FK→users  | cascade delete                 |
| name            | VARCHAR(120)  | host display name              |
| email           | VARCHAR(200)  |                                |
| title           | VARCHAR(200)  | activity title                 |
| description     | TEXT          |                                |
| location        | VARCHAR(200)  |                                |
| price           | DECIMAL(10,2) |                                |
| image_filename  | VARCHAR(255)  | stored in `static/images/`     |
| category        | VARCHAR(100)  | default "Other"                |
| session_link    | VARCHAR(255)  | Zoom/Meet link for online sessions |
| created_at      | TIMESTAMP     |                                |

### `user_bookings`
| Column        | Type                   | Notes            |
|---------------|------------------------|------------------|
| id            | INT PK AI              |                  |
| user_id       | INT FK→users           | cascade delete   |
| activity_id   | INT FK→host_activity   | cascade delete   |
| booking_date  | DATE                   |                  |
| created_at    | TIMESTAMP              |                  |

### `enrollments`
| Column      | Type                 | Notes                              |
|-------------|----------------------|------------------------------------|
| id          | INT PK AI            |                                    |
| activity_id | INT FK→host_activity | cascade delete                     |
| user_name   | VARCHAR(120)         |                                    |
| user_email  | VARCHAR(200)         |                                    |
| note        | TEXT                 | optional message to host           |
| created_at  | TIMESTAMP            |                                    |

---

## Features

### User Authentication

- **Register** — creates a new account with a bcrypt-hashed password. Duplicate username or email shows an error flash message.
- **Login** — verifies credentials, sets `session["user_id"]`, `session["username"]`, and `session["is_host"]`. Automatically redirects hosts to the Host Dashboard and regular users to the User Dashboard.
- **Logout** — clears the entire session and redirects to the login page.
- All protected routes check `session["user_id"]` and redirect unauthenticated users to login.

---

### Home & Search

- Displays a combined feed of **static/dummy experiences** (hardcoded) and **community-hosted activities** fetched from the database.
- **Search bar** (`?q=`) filters results by activity title or location — applied to both dummy and DB-hosted cards in a single merged list.
- Each card shows: title, location, price, cover image, and category.
- Requires login to access.

---

### Category Browsing

Five categories are supported:

| Category      | Slug           |
|---------------|----------------|
| Art & Culture | `art-culture`  |
| Culinary Arts | `culinary-arts`|
| Dance         | `dance`        |
| Wellness      | `wellness`     |
| Adventure     | `adventure`    |

- Visiting `/category/<slug>` shows up to 3 experiences (static + hosted) matching that category.
- Category cards on the home page link to the relevant category page.

---

### Experience Detail

- Route: `/experience_detail/<id>`
- For **dummy/static** experiences (id < 10 000): shows the hardcoded data and a booking form.
- For **hosted** experiences (id ≥ 10 000, offset = 10 000 + DB id): fetches live data from the `host_activity` table and shows the host name, session link, and an **Enroll** button.

---

### Booking System

- Route: `POST /book/<id>`
- Only available for community-hosted activities (id ≥ 10 000).
- User selects a booking date; the record is inserted into `user_bookings`.
- Requires login; missing date returns HTTP 400.
- On success, user is redirected to their Dashboard with a flash confirmation.

---

### Enrollment System

- Route: `GET/POST /enroll/<id>`
- Separate from booking — specifically for community-hosted sessions that may have an online teaching/meeting component.
- User submits name, email, and an optional note.
- Data is stored in the `enrollments` table.
- After enrolling, the user is told the host will share the session link.
- Only valid for hosted activities (id ≥ 10 000).

---

### User Dashboard

- Route: `/dashboard`
- Shows all bookings made by the logged-in user: activity title, location, price, and booking date.
- Data is fetched by joining `user_bookings` → `host_activity`.
- Requires login.

---

### Become a Host

- Route: `POST /become-host`
- Any logged-in user can register as a host by submitting:
  - **Name** — display name shown on the activity card
  - **Activity title**
  - **Description**
  - **Location**
  - **Price** (decimal)
  - **Category** (Art & Culture, Culinary Arts, Dance, Wellness, Adventure, or Other)
  - **Session link** — optional Zoom/Meet/etc. URL
  - **Image upload** — saved to `static/images/` with a UUID prefix
- The activity is inserted into `host_activity` with the logged-in user's ID.
- `session["is_host"]` is set to `True` and the user is redirected to the Host Dashboard.

---

### Host Dashboard

- Route: `/host/dashboard`
- Only accessible if the logged-in user has at least one activity in `host_activity`.
- Lists all of the host's activities with:
  - Activity title and category
  - Cover image thumbnail
  - **Total bookings** count (aggregated via LEFT JOIN on `user_bookings`)
- Each activity links to the booking details page.

---

### Host Activity Management

**View bookings per activity**
- Route: `/host/activity/<activity_id>`
- Shows a table of all users who booked a specific activity: username, email, booking date.
- Validates that the activity belongs to the logged-in host.

**Manage session link & enrollments**
- Route: `GET/POST /host/manage/<db_id>`
- Host can view and update the session/meeting link for an activity.
- Lists all enrolled users (name, email, optional note, enrollment date).

---

### About Page (Blogs & Reels)

- Route: `/about`
- Reads from the `static/uploads/` folder:
  - **Blogs** — `.txt` files are read and displayed as blog posts (filename becomes the title).
  - **Reels** — `.mp4`, `.mov`, `.avi`, `.mkv` video files are listed.

**Upload content**
- Route: `POST /upload_experience`
- **Blog upload**: submit a title + text content → saved as `<title>.txt` in `static/uploads/`.
- **Reel upload**: submit a video file → saved with a UUID prefix in `static/uploads/`.
- Invalid/incomplete submissions show a warning flash message.

---

## Routes Reference

| Method   | Route                          | Description                              |
|----------|--------------------------------|------------------------------------------|
| GET      | `/`                            | Home page (search + experience feed)     |
| GET      | `/category/<slug>`             | Category filtered experience list        |
| GET      | `/about`                       | About page with blogs and reels          |
| POST     | `/upload_experience`           | Upload a blog post or video reel         |
| GET/POST | `/host`                        | Host enquiry form (static)               |
| POST     | `/become-host`                 | Submit a new hosted activity             |
| GET      | `/experience_detail/<id>`      | Experience detail view                   |
| GET/POST | `/enroll/<id>`                 | Enroll in a hosted activity              |
| GET/POST | `/host/manage/<db_id>`         | Manage session link + view enrollments   |
| GET      | `/dashboard`                   | User booking dashboard                   |
| POST     | `/book/<id>`                   | Book a hosted activity                   |
| GET/POST | `/register`                    | User registration                        |
| GET/POST | `/login`                       | User login                               |
| GET      | `/logout`                      | Logout and clear session                 |
| GET      | `/host/dashboard`              | Host dashboard (activities + bookings)   |
| GET      | `/host/activity/<activity_id>` | Bookings for a specific activity         |

---

## Project Structure

```
.
├── app.py                  # Main Flask application
├── static/
│   ├── css/
│   │   └── style.css
│   ├── images/             # Activity cover images (host uploads + static assets)
│   ├── js/
│   │   └── main.js
│   └── uploads/            # Blog .txt files, video reels, hosted activity images
│       └── host_activity/
└── templates/
    ├── base.html
    ├── home.html
    ├── about.html
    ├── category.html
    ├── experience_detail.html
    ├── dashboard.html
    ├── host.html
    ├── host_dashboard.html
    ├── host_activity_bookings.html
    ├── login.html
    └── register.html
```

---

## Setup & Running

### Prerequisites

- Python 3.10+
- MySQL server running locally

### 1. Install dependencies

```bash
pip install flask flask-bcrypt mysql-connector-python werkzeug
```

### 2. Configure the database

Edit the connection block in `app.py`:

```python
db = mysql.connector.connect(
    host="localhost",
    user="root",
    password="YOUR_PASSWORD",
    database="mywebsite"
)
```

Create the database in MySQL:

```sql
CREATE DATABASE mywebsite;
```

Tables are created automatically on first run via `ensure_tables()`.

### 3. Run the application

```bash
python app.py
```

The app starts in debug mode on `http://127.0.0.1:5000`.

> **Note:** `use_reloader=False` is set to prevent the MySQL cursor from being duplicated on hot-reload.
