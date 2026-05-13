"""
app/api/v1/auth.py
==================
JWT authentication endpoints.
Uses bcrypt directly instead of passlib to avoid version compatibility issues.

POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/refresh
GET  /api/v1/auth/me
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import auth_attempts_total
from app.models.user import User
from app.schemas.auth import Token, TokenData, UserCreate, UserLogin
from app.services.database import get_db

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ── Password hashing (direct bcrypt — no passlib) ─────────────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt. Truncates to 72 bytes (bcrypt hard limit)."""
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    try:
        plain_bytes = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(plain_bytes, hashed.encode("utf-8"))
    except Exception:
        return False


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    payload = {
        **data,
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict) -> str:
    payload = {
        **data,
        "exp": datetime.now(timezone.utc)
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency — validate JWT and return the User ORM object."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise credentials_exception
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> Token:
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="A user with this email already exists.")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.company,
    )
    db.add(user)
    await db.flush()  
    # Ensure the transaction is committed so the user actually exists in the DB
    await db.refresh(user) # Syncs state back from DB
    token_data = {"sub": str(user.id), "email": user.email}
    log.info("user registered", user_id=str(user.id))
    auth_attempts_total.labels(endpoint="register", status="success").inc()
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        full_name=user.full_name, # Include the user's full_name (company name)
    )


@router.post("/login", response_model=Token)
async def login(
    payload: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> Token:
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        auth_attempts_total.labels(endpoint="login", status="failure").inc()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Incorrect email or password")
    token_data = {"sub": str(user.id), "email": user.email}
    auth_attempts_total.labels(endpoint="login", status="success").inc()
    log.info("user logged in", user_id=str(user.id))
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        full_name=user.full_name, # Include the user's full_name (company name)
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db)) -> Token:
    try:
        payload = jwt.decode(
            refresh_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    token_data = {"sub": str(user.id), "email": user.email}
    return Token(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

@router.post("/logout")
async def logout(response: Response):
    """
    The ONLY way to clear HttpOnly cookies is via a Set-Cookie header from the server.
    """
    cookies_to_clear = ["access_token", "refresh_token", "session_active"]
    for cookie in cookies_to_clear:
        response.delete_cookie(
            key=cookie,
            path="/",
            httponly=True if cookie != "session_active" else False,
            samesite="lax",
        )
    return {"detail": "Successfully logged out"}


@router.get("/me")
async def get_me(current_user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "company": current_user.full_name,
        "is_active": current_user.is_active,
    }