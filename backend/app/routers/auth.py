import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.services import database
from app.services.performance import measure_stage

router = APIRouter()
SESSION_DAYS = 30


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, expected = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        actual = _password_hash(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _issue_session(user_id: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    database.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (token_hash, user_id, expires),
    )
    return token, expires


async def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with measure_stage("authentication"):
        session = await run_in_threadpool(
            database.fetch_one,
            """SELECT users.id, users.name, users.email, sessions.expires_at
               FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ?""",
            (token_hash,),
        )
    if not session:
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    expiry = datetime.fromisoformat(session["expires_at"])
    if expiry <= datetime.now(timezone.utc):
        await run_in_threadpool(
            database.execute, "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )
        raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return {"id": session["id"], "name": session["name"], "email": session["email"]}


def _auth_response(user: dict) -> dict:
    token, expires = _issue_session(user["id"])
    return {"success": True, "token": token, "expires_at": expires, "user": {
        "id": user["id"], "name": user["name"], "email": user["email"]
    }}


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest):
    name = request.name.strip()
    email = request.email.strip().lower()
    if not name:
        raise HTTPException(422, "Name is required.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Enter a valid email address.")
    if database.fetch_one("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)):
        raise HTTPException(409, "An account with this email already exists.")
    user = {"id": str(uuid.uuid4()), "name": name, "email": email}
    try:
        database.execute(
            "INSERT INTO users (id, name, email, password_hash) VALUES (?, ?, ?, ?)",
            (user["id"], name, email, _password_hash(request.password)),
        )
    except Exception as exc:
        if database.fetch_one("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)):
            raise HTTPException(409, "An account with this email already exists.") from exc
        raise
    return _auth_response(user)


@router.post("/login")
def login(request: LoginRequest):
    email = request.email.strip().lower()
    user = database.fetch_one("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,))
    if not user or not _password_matches(request.password, user["password_hash"]):
        raise HTTPException(401, "Email or password is incorrect.")
    return _auth_response(user)


@router.get("/me")
def current_user(user: dict = Depends(get_current_user)):
    return {"success": True, "user": user}


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None), user: dict = Depends(get_current_user)):
    token = authorization[7:].strip()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    await run_in_threadpool(
        database.execute,
        "DELETE FROM sessions WHERE token_hash = ? AND user_id = ?",
        (token_hash, user["id"]),
    )
    return {"success": True, "message": "Signed out."}
