# Social Media App (Option A)

This repository contains my Social Media project for Principles of Software Methodology (Fall 2026): a simple social media web app built with a FastAPI backend and PostgreSQL database. The app supports account registration and login, token-based authentication, and basic data operations for social-media-style features. This project is for course demonstration purposes only.

## Architecture

- **UI**: Browser-based interface (HTML pages served by FastAPI)
- **API**: FastAPI service on Render (handles auth and data endpoints)
- **Database host**: Supabase PostgreSQL

**Data flow**:
1. User interacts with the browser UI.
2. UI sends HTTP requests to the FastAPI API.
3. FastAPI reads/writes data in Supabase Postgres using `DATABASE_URL`.
4. FastAPI returns JSON/HTML responses back to the UI.

## Demo Accounts

| Email | Password |
|---|---|
| alex@example.com | ClassDemo123! |
| johndoe@example.com | PASSWORD |
| IHeartChezbrgrz@example.com | CHEESEBURGER |

## API Table

| Method | Path | Auth required? | Purpose |
|---|---|---|---|
| GET | `/health` | No | Health check endpoint for uptime/status |
| POST | `/api/register` | No | Register a new user account |
| POST | `/api/login` | No | Log in and return bearer token |
| POST | `/api/posts` | Yes | Create a post (write operation) |
| GET | `/api/posts` | Yes | Read/list posts (read operation) |

## cURL Examples


### 1) Health

```bash
curl -sS https://prin-of-software-methodology-project-m.onrender.com/health
```

### 2) Register (or Login)

```bash
curl -sS -X POST https://prin-of-software-methodology-project-m.onrender.com/api/register \
  -H "Content-Type: application/json" \
  -d '{"email":"demo1@example.com","password":"DemoPass123!"}'
```

If the user already exists, use login:

```bash
curl -sS -X POST https://prin-of-software-methodology-project-m.onrender.com/api/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo1@example.com","password":"DemoPass123!"}'
```

### 3) Data Write (create post)

```bash
curl -sS -X POST "https://prin-of-software-methodology-project-m.onrender.com/api/messages" -H "Content-Type: application/json" -H "Authorization: Bearer <access-token-here>" -d '{"body":"hello from curl"}'
```

### 4) Data Read (list posts)

```bash
curl -sS "https://prin-of-software-methodology-project-m.onrender.com/api/messages" -H 'Authorization: Bearer <access-token-here>'
```

## Non-Production Notice

This is **not** a production system. It is a class project only, and all data is fake/demo data.
