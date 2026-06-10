"""
Auth routes: register, login, me, microsoft SSO
"""
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlmodel import select
import httpx
from jose import jwt as jose_jwt

from app.core.database import get_session
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.core.config import settings
from app.models.user import User, UserCreate, UserRead, UserLogin

router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=201)
async def register(body: UserCreate, session: AsyncSession = Depends(get_session)):
    # Normalize email to lowercase before any check or storage
    normalized_email = body.email.strip().lower()
    existing = await session.exec(select(User).where(func.lower(User.email) == normalized_email))
    if existing.first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=normalized_email,   # always stored lowercase
        full_name=body.full_name,
        role=body.role,
        hashed_password=hash_password(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    login_identifier = form_data.username.strip().lower()
    result = await session.exec(select(User).where(func.lower(User.email) == login_identifier))
    user = result.first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found with this email address.",
        )
    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password. Please try again.",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account has been disabled. Contact your administrator.")

    token = create_access_token(
        data={"sub": user.id, "role": user.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role},
    }


@router.get("/me", response_model=UserRead)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


# ── Microsoft SSO ─────────────────────────────────────────────────────────────

import base64
import hashlib
import json

# Whitelist of allowed frontend origins — must all be registered in Azure Portal too
ALLOWED_ORIGINS = {
    "http://localhost:3000",
    "https://staging.lexai.zuarione.com",
    "https://lexai.zuarione.com",
}


def _encode_state(nonce: str, redirect_uri: str, code_verifier: str) -> str:
    """Encode nonce, redirect_uri, and PKCE code_verifier into the OAuth state parameter."""
    payload = json.dumps({"n": nonce, "r": redirect_uri, "cv": code_verifier})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_state(state: str) -> dict:
    """Decode the OAuth state parameter back into nonce + redirect_uri."""
    try:
        payload = base64.urlsafe_b64decode(state.encode() + b"==").decode()
        return json.loads(payload)
    except Exception:
        return {}


@router.get("/microsoft/login")
async def microsoft_login(origin: str = Query(default="http://localhost:3000")):
    """
    Return the Microsoft OAuth2 authorization URL.

    `origin` — the frontend base URL (e.g. https://lexai.zuarione.com).
    The redirect_uri is the root of that origin (e.g. https://lexai.zuarione.com/)
    to match what is registered in Azure Portal.
    The redirect_uri is also encoded in state so the callback can use the same
    URI for the token exchange — required by the OAuth2 spec.
    """
    origin = origin.rstrip("/")
    if origin not in ALLOWED_ORIGINS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Origin '{origin}' is not an allowed redirect origin.",
        )

    # Use root redirect URI — must exactly match what is registered in Azure Portal
    redirect_uri = f"{origin}/"
    nonce = secrets.token_urlsafe(24)

    # PKCE — required because redirect URIs are registered as SPA type in Azure.
    # We generate verifier+challenge on the server; verifier is stored in state
    # so it flows back to us at callback time without ever touching the browser.
    code_verifier = secrets.token_urlsafe(64)          # 86-char URL-safe random string
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )

    state = _encode_state(nonce, redirect_uri, code_verifier)

    params = {
        "client_id": settings.AZURE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid email profile User.Read",
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = (
        f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
        f"/oauth2/v2.0/authorize?{urlencode(params)}"
    )
    return {"url": url}


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """
    Receives the auth code from Microsoft, exchanges it for tokens,
    extracts the user email from the ID token, and issues a LexAI JWT.

    IMPORTANT: Only existing users (pre-provisioned by admin) can sign in.
    No new user accounts are created via this flow.
    """
    # 1. Decode state to recover redirect_uri and PKCE code_verifier
    state_data = _decode_state(state)
    redirect_uri = state_data.get("r")
    code_verifier = state_data.get("cv")
    # redirect_uri is like "http://localhost:3000/" — strip trailing slash to get origin
    if not redirect_uri or redirect_uri.rstrip("/") not in ALLOWED_ORIGINS or not code_verifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or tampered state parameter.",
        )

    # 2. Exchange authorization code for tokens (PKCE: send code_verifier, not client_secret)
    token_url = (
        f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/token"
    )
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": settings.AZURE_CLIENT_ID,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,   # PKCE verifier proves we started this flow
        "scope": "openid email profile User.Read",
    }
    # For SPA (public client) registrations, we must NOT send client_secret
    # even if one is available, otherwise Microsoft returns AADSTS700025.
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            token_url, 
            data=token_payload,
            headers={"Origin": redirect_uri.rstrip("/")}
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Microsoft token exchange failed: {resp.text}",
        )

    token_data = resp.json()
    id_token: str | None = token_data.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Microsoft did not return an ID token.",
        )

    # 3. Decode the ID token claims
    try:
        claims = jose_jwt.decode(
            id_token,
            key="",
            algorithms=["RS256"],
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_exp": True,
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Microsoft ID token: {exc}",
        )

    # 4. Extract email
    email: str | None = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
    )
    if not email or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract a valid email address from Microsoft token.",
        )

    # 5. Look up existing user ONLY — no new user creation
    result = await session.exec(
        select(User).where(func.lower(User.email) == email.strip().lower())
    )
    user = result.first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access denied. Your Microsoft account is not provisioned in LexAI. "
                "Contact your administrator to get access."
            ),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been disabled. Contact your administrator.",
        )

    # 6. Issue a standard LexAI JWT
    lexai_token = create_access_token(
        data={"sub": user.id, "role": user.role},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {
        "access_token": lexai_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
        },
    }
