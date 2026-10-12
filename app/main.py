from fastapi import FastAPI, Request, Depends, HTTPException, status, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, func, select, and_
import os
from . import db, models, auth
from .db import get_db
from .schemas import UserCreate, Token, MessageCreate, FriendshipCreate

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

def _page_params(page: int, page_size: int):
    return max(page, 1), min(max(page_size, 1), 100)


def _feed_query(db: Session, author_id: int | None = None, friends_of: int | None = None):
    """messages JOIN users (+ likes count) (+ friendships when friends_of is set)."""
    like_count = (
        select(func.count(models.Like.id))
        .where(models.Like.message_id == models.Message.id)
        .correlate(models.Message)
        .scalar_subquery()
    )
    q = (
        db.query(
            models.Message.id,
            models.Message.user_id,
            models.User.email.label("user_email"),
            models.Message.body,
            models.Message.created_at,
            like_count.label("like_count"),
        )
        .join(models.User, models.User.id == models.Message.user_id)
    )
    if author_id is not None:
        q = q.filter(models.Message.user_id == author_id)
    if friends_of is not None:
        q = q.join(
            models.Friendship,
            and_(models.Friendship.friend_id == models.Message.user_id,
                 models.Friendship.user_id == friends_of),
        )
    return q


def _paginate(q, page: int, page_size: int):
    page, page_size = _page_params(page, page_size)
    total = q.with_entities(func.count(models.Message.id)).order_by(None).scalar()
    rows = (
        q.order_by(models.Message.created_at.desc(), models.Message.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [dict(r._mapping) for r in rows]
    pages = max((total + page_size - 1) // page_size, 1)
    return {"items": items, "page": page, "page_size": page_size, "total": total, "pages": pages}


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
    token = auth.authenticate_user(db, email, password)
    response = RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, samesite="lax")
    return response


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
    response = RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="access_token", value=f"Bearer {token}", httponly=True, samesite="lax")
    return response


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


# Helper to extract token from Authorization header or cookie
def get_token_from_request(request: Request = None, authorization: str = Header(None)) -> str:
    # Try Authorization header first
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    # Fall back to cookie
    if request is not None:
        raw = request.cookies.get("access_token")
        if raw:
            return raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    raise HTTPException(status_code=401, detail="Missing Authorization header")


def get_token_from_header(authorization: str = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return parts[1]


# ============ MESSAGES API ============

@app.get("/feed", response_class=HTMLResponse)
def feed_page(request: Request, page: int = 1, db: Session = Depends(get_db)):
    """Feed page (shows all messages)."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        current_user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    messages = db.query(models.Message).order_by(models.Message.created_at.desc()).all()
    result = []

    
    pg = _paginate(_feed_query(db), page, 20)
    result = pg["items"]

    friendships = db.query(models.Friendship).filter(models.Friendship.user_id == current_user_id).all()
    friend_ids = {f.friend_id for f in friendships}
    friends = db.query(models.User).filter(models.User.id.in_(friend_ids)).all() if friend_ids else []
    users = db.query(models.User).filter(models.User.id != current_user_id).order_by(models.User.email.asc()).all()
    discoverable_users = [
        {"id": u.id, "email": u.email, "is_friend": u.id in friend_ids}
        for u in users
    ]
    return templates.TemplateResponse(
        "feed.html",
        {
            "request": request,
            "posts": result,
            "pg": pg,
            "current_user_id": current_user_id,
            "friends": friends,
            "users": discoverable_users,
            "error": request.query_params.get("error"),
            "success": request.query_params.get("success"),
        },
    )


@app.post("/posts")
async def create_post_form(request: Request, db: Session = Depends(get_db)):
    """Create a post from the web form."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    form = await request.form()
    body = form.get("body", "").strip()
    if body:
        db_msg = models.Message(user_id=user_id, body=body)
        db.add(db_msg)
        db.commit()
    return RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/posts/{msg_id}/like")
def like_post_form(msg_id: int, request: Request, db: Session = Depends(get_db)):
    """Like a post from the HTML feed."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    db_msg = db.query(models.Message).filter(models.Message.id == msg_id).first()
    if not db_msg:
        return RedirectResponse(url="/feed?error=Message+not+found", status_code=status.HTTP_303_SEE_OTHER)

    existing = db.query(models.Like).filter(
        models.Like.message_id == msg_id,
        models.Like.user_id == user_id,
    ).first()
    if existing:
        return RedirectResponse(url="/feed?error=You+already+liked+this+post", status_code=status.HTTP_303_SEE_OTHER)

    db.add(models.Like(message_id=msg_id, user_id=user_id))
    db.commit()
    return RedirectResponse(url="/feed?success=Post+liked", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/posts/{msg_id}/edit", response_class=HTMLResponse)
def edit_post_form_page(msg_id: int, request: Request, db: Session = Depends(get_db)):
    """Show the edit form for a post owner."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    db_msg = db.query(models.Message).filter(models.Message.id == msg_id).first()
    if not db_msg:
        return RedirectResponse(url="/feed?error=Message+not+found", status_code=status.HTTP_302_FOUND)
    if db_msg.user_id != user_id:
        return RedirectResponse(url="/feed?error=Not+authorized", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        "edit_post.html",
        {
            "request": request,
            "post": db_msg,
            "error": request.query_params.get("error"),
        },
    )


@app.post("/posts/{msg_id}/edit")
async def edit_post_form(msg_id: int, request: Request, db: Session = Depends(get_db)):
    """Update a post from the web form."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    db_msg = db.query(models.Message).filter(models.Message.id == msg_id).first()
    if not db_msg:
        return RedirectResponse(url="/feed?error=Message+not+found", status_code=status.HTTP_303_SEE_OTHER)
    if db_msg.user_id != user_id:
        return RedirectResponse(url="/feed?error=Not+authorized", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    body = form.get("body", "").strip()
    if not body:
        return templates.TemplateResponse(
            "edit_post.html",
            {
                "request": request,
                "post": db_msg,
                "error": "Post body required",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    db_msg.body = body
    db.commit()
    db.refresh(db_msg)
    return RedirectResponse(url="/feed?success=Post+updated", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/friends/add")
async def add_friend_form(request: Request, db: Session = Depends(get_db)):
    """Add a friend from the HTML feed."""
    raw = request.cookies.get("access_token")
    if not raw:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    token = raw[len("Bearer "):] if raw.startswith("Bearer ") else raw
    try:
        user_id = auth.get_current_user_id(token)
    except Exception:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    friend_id_raw = form.get("friend_id")
    try:
        friend_id = int(friend_id_raw)
    except (TypeError, ValueError):
        return RedirectResponse(url="/feed?error=Invalid+friend+selection", status_code=status.HTTP_303_SEE_OTHER)

    if friend_id == user_id:
        return RedirectResponse(url="/feed?error=Cannot+add+yourself+as+a+friend", status_code=status.HTTP_303_SEE_OTHER)

    friend = db.query(models.User).filter(models.User.id == friend_id).first()
    if not friend:
        return RedirectResponse(url="/feed?error=User+not+found", status_code=status.HTTP_303_SEE_OTHER)

    existing = db.query(models.Friendship).filter(
        models.Friendship.user_id == user_id,
        models.Friendship.friend_id == friend_id,
    ).first()
    if existing:
        return RedirectResponse(url="/feed?error=You+are+already+friends", status_code=status.HTTP_303_SEE_OTHER)

    db.add(models.Friendship(user_id=user_id, friend_id=friend_id))
    db.commit()
    return RedirectResponse(url="/feed?success=Friend+added", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/logout")
def logout():
    """Clear the auth cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="access_token", httponly=True, samesite="lax")
    return response



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


# 3 tables (messages + users + likes), LIST
@app.get("/api/messages")
def list_messages(page: int = Query(1), page_size: int = Query(20), db: Session = Depends(get_db)):
    return _paginate(_feed_query(db), page, page_size)

# 2 tables (messages + users), SINGLE
@app.get("/api/messages/{msg_id}")
def get_message(msg_id: int, db: Session = Depends(get_db)):
    row = (db.query(models.Message.id, models.Message.body, models.Message.created_at,
                    models.User.id.label("user_id"), models.User.email.label("user_email"))
           .join(models.User, models.User.id == models.Message.user_id)
           .filter(models.Message.id == msg_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    return dict(row._mapping)

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


# 1 table, LIST
@app.get("/api/users")
def list_users(page: int = Query(1), page_size: int = Query(20), db: Session = Depends(get_db)):
    page, page_size = _page_params(page, page_size)
    total = db.query(func.count(models.User.id)).scalar()
    rows = (db.query(models.User.id, models.User.email)
            .order_by(models.User.id).offset((page - 1) * page_size).limit(page_size).all())
    return {"items": [dict(r._mapping) for r in rows], "page": page,
            "page_size": page_size, "total": total}

# 1 table, SINGLE
@app.get("/api/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    u = db.query(models.User.id, models.User.email, models.User.created_at)\
          .filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(u._mapping)

# 3 tables (messages + users + likes), LIST filtered by author
@app.get("/api/users/{user_id}/messages")
def list_user_messages(user_id: int, page: int = Query(1), page_size: int = Query(20),
                       db: Session = Depends(get_db)):
    return _paginate(_feed_query(db, author_id=user_id), page, page_size)

# 4 tables (messages + users + friendships + likes), LIST
@app.get("/api/feed/friends")
def friends_feed(page: int = Query(1), page_size: int = Query(20),
                 db: Session = Depends(get_db), authorization: str = Header(None)):
    user_id = auth.get_current_user_id(get_token_from_header(authorization))
    return _paginate(_feed_query(db, friends_of=user_id), page, page_size)

# 4 tables (users + messages + likes + friendships), SINGLE aggregate
@app.get("/api/users/{user_id}/summary")
def user_summary(user_id: int, db: Session = Depends(get_db)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    msgs = db.query(func.count(models.Message.id)).filter(models.Message.user_id == user_id).scalar()
    likes_received = (db.query(func.count(models.Like.id))
                      .join(models.Message, models.Message.id == models.Like.message_id)
                      .filter(models.Message.user_id == user_id).scalar())
    friends = db.query(func.count(models.Friendship.id)).filter(models.Friendship.user_id == user_id).scalar()
    return {"id": u.id, "email": u.email, "message_count": msgs,
            "likes_received": likes_received, "friend_count": friends}


# ============ HTML UI ============

@app.get("/friends", response_class=HTMLResponse)
def friends_page():
    """Friends are managed from the feed page."""
    return RedirectResponse(url="/feed", status_code=status.HTTP_302_FOUND)
