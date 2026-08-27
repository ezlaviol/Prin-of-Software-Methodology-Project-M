# Social Media baseline app

This is a minimal FastAPI app for the "Social Media" project for your class. It includes user registration and login (email/password) with JWT authentication, a health endpoint, and a simple HTML UI to register and login.

Requirements
- Python 3.10+
- PostgreSQL database (provide connection via DATABASE_URL environment variable). For local development, the app falls back to a local SQLite file (dev.db) if DATABASE_URL is not set — do NOT use SQLite in production.

Setup (local)

1. Create a `.env` file in the project root with the following values:

```
DATABASE_URL=postgresql://username:password@host:port/dbname
SECRET_KEY=replace-this-with-a-secret
PORT=8000
```

2. Install dependencies:

pip install -r requirements.txt

3. Run the app:

# set PORT if you want, otherwise defaults to 8000
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

Endpoints
- GET /health — returns {"status": "ok"}
- GET / — HTML home page
- GET /register — HTML registration form
- POST /register — form submit (HTML)
- POST /api/register — JSON register: {"email": "...", "password": "..."}
- GET /login — HTML login form
- POST /login — form submit (HTML)
- POST /api/login — JSON login: {"email": "...", "password": "..."} -> returns {"access_token": "...", "token_type": "bearer"}

Notes
- The app expects `DATABASE_URL` (Postgres) in production. You can use Supabase for hosting Postgres and provide the full DATABASE_URL.
- The JWT secret is taken from SECRET_KEY environment variable; set this in production.

What's next
I implemented the project skeleton and the health/register/login flows plus HTML pages. Tell me to run your tests or to continue: I'll add the friends/messages API and matching HTML UI next.
