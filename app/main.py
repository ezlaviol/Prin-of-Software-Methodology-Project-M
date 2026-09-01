from fastapi import FastAPI, Request, Depends, HTTPException, status, Header
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import os
from . import db, models, auth
from .db import get_db
from .schemas import UserCreate, Token, MessageCreate, FriendshipCreate

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


# Helper to extract token from Authorization header
def get_token_from_header(authorization: str = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return parts[1]


# ============ MESSAGES API ============

@app.get("/feed", response_class=HTMLResponse)
def feed_page(request: Request, token: str = Header(None, alias="authorization")):
    """Feed page (shows all messages)."""
    if not token:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    # Extract token from "Bearer <token>" format
    if token.startswith("Bearer "):
        token = token[7:]
    return templates.TemplateResponse("feed.html", {"request": request, "token": token})


@app.post("/api/messages")
def create_message(
    msg: MessageCreate,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    """Create a new message/post."""
    token = get_token_from_header(authorization)
    user_id = auth.get_current_user_id(token)
    
    db_msg = models.Message(user_id=user_id, body=msg.body)
    db.add(db_msg)
    db.commit()
    db.refresh(db_msg)
    return {"id": db_msg.id, "user_id": db_msg.user_id, "body": db_msg.body, "created_at": db_msg.created_at}


@app.get("/api/messages")
def list_messages(db: Session = Depends(get_db), authorization: str = Header(None)):
    """List all messages (feed)."""
    if not authorization:
        # Allow unauthenticated access to list messages
        messages = db.query(models.Message).order_by(models.Message.created_at.desc()).all()
    else:
        token = authorization.split()[-1] if " " in authorization else authorization
        try:
            user_id = auth.get_current_user_id(token)
        except HTTPException:
            # Fall back to listing all if token is invalid
            messages = db.query(models.Message).order_by(models.Message.created_at.desc()).all()
            return [{"id": m.id, "user_id": m.user_id, "body": m.body, "created_at": m.created_at} for m in messages]
        messages = db.query(models.Message).order_by(models.Message.created_at.desc()).all()
    
    result = []
    for m in messages:
        user = db.query(models.User).filter(models.User.id == m.user_id).first()
        like_count = db.query(models.Like).filter(models.Like.message_id == m.id).count()
        result.append({
            "id": m.id,
            "user_id": m.user_id,
            "user_email": user.email if user else "Unknown",
            "body": m.body,
            "created_at": m.created_at,
            "like_count": like_count,
        })
    return result


@app.patch("/api/messages/{msg_id}")
def edit_message(
    msg_id: int,
    msg: MessageCreate,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    """Edit a message (only owner can edit)."""
    token = get_token_from_header(authorization)
    user_id = auth.get_current_user_id(token)
    
    db_msg = db.query(models.Message).filter(models.Message.id == msg_id).first()
    if not db_msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if db_msg.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    db_msg.body = msg.body
    db.commit()
    db.refresh(db_msg)
    return {"id": db_msg.id, "body": db_msg.body, "updated_at": db_msg.updated_at}


@app.post("/api/messages/{msg_id}/like")
def like_message(
    msg_id: int,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    """Like a message."""
    token = get_token_from_header(authorization)
    user_id = auth.get_current_user_id(token)
    
    db_msg = db.query(models.Message).filter(models.Message.id == msg_id).first()
    if not db_msg:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if already liked
    existing = db.query(models.Like).filter(
        models.Like.message_id == msg_id,
        models.Like.user_id == user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already liked")
    
    like = models.Like(message_id=msg_id, user_id=user_id)
    db.add(like)
    db.commit()
    return {"msg": "liked"}


# ============ FRIENDS API ============

@app.post("/api/friends")
def add_friend(
    friend_req: FriendshipCreate,
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    """Add a friend."""
    token = get_token_from_header(authorization)
    user_id = auth.get_current_user_id(token)
    
    friend_id = friend_req.user_id
    if friend_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as a friend")
    
    # Check if friend exists
    friend = db.query(models.User).filter(models.User.id == friend_id).first()
    if not friend:
        raise HTTPException(status_code=404, detail="Friend not found")
    
    # Check if already friends
    existing = db.query(models.Friendship).filter(
        models.Friendship.user_id == user_id,
        models.Friendship.friend_id == friend_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already friends")
    
    friendship = models.Friendship(user_id=user_id, friend_id=friend_id)
    db.add(friendship)
    db.commit()
    db.refresh(friendship)
    return {"id": friendship.id, "user_id": friendship.user_id, "friend_id": friendship.friend_id}


@app.get("/api/friends")
def list_friends(
    db: Session = Depends(get_db),
    authorization: str = Header(None),
):
    """List friends of the current user."""
    token = get_token_from_header(authorization)
    user_id = auth.get_current_user_id(token)
    
    friendships = db.query(models.Friendship).filter(models.Friendship.user_id == user_id).all()
    result = []
    for f in friendships:
        friend = db.query(models.User).filter(models.User.id == f.friend_id).first()
        if friend:
            result.append({"id": friend.id, "email": friend.email})
    return result


@app.get("/api/users")
def list_users(db: Session = Depends(get_db)):
    """List all users (for discovery)."""
    users = db.query(models.User).all()
    return [{"id": u.id, "email": u.email} for u in users]


# ============ HTML UI ============

@app.get("/friends", response_class=HTMLResponse)
def friends_page(request: Request, token: str = Header(None, alias="authorization")):
    """Friends page."""
    if not token:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    # Extract token from "Bearer <token>" format
    if token.startswith("Bearer "):
        token = token[7:]
    return templates.TemplateResponse("friends.html", {"request": request, "token": token})
