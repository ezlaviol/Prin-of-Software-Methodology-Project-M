from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os
from . import db, models, auth
from .db import get_db
from .schemas import UserCreate, Token

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def on_startup():
    # create tables
    models.Base.metadata.create_all(bind=db.engine)


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register_form_post(request: Request, db=Depends(get_db)):
    form = await request.form()
    email = form.get("email")
    password = form.get("password")
    if not email or not password:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email and password required"})
    user_in = UserCreate(email=email, password=password)
    try:
        user = auth.create_user(db, user_in)
    except ValueError as e:
        return templates.TemplateResponse("register.html", {"request": request, "error": str(e)})
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_form_post(request: Request, db=Depends(get_db)):
    form = await request.form()
    email = form.get("email")
    password = form.get("password")
    if not email or not password:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Email and password required"})
    token = auth.authenticate_user_for_html(db, email, password)
    if not token:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    # show token to user
    return templates.TemplateResponse("token.html", {"request": request, "token": token})


# JSON API endpoints for register/login
@app.post("/api/register", response_model=dict)
def api_register(user: UserCreate, db=Depends(get_db)):
    try:
        auth.create_user(db, user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(status_code=201, content={"msg": "user created"})


@app.post("/api/login", response_model=Token)
def api_login(user: UserCreate, db=Depends(get_db)):
    token = auth.authenticate_user(db, user.email, user.password)
    if not token:
        raise HTTPException(status_code=400, detail="Invalid credentials")
    return {"access_token": token, "token_type": "bearer"}
