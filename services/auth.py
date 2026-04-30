import os
import secrets
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from dotenv import load_dotenv

load_dotenv()

# Get Constants from .env
SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 3

def create_access_token(user_id: str, role: str) -> str:
    """
    Payload to include sub, role, iat(issued at), and exp.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": expire
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token() -> str:
    """
    Uses 'secrets' module for a cryptographically secure random string.
    """
    return secrets.token_urlsafe(64)

def verify_access_token(token: str):
    """
    Decodes and validates the access token. Returns the payload if valid, raises JWTError if not.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# ── Stateless OAuth state tokens (CSRF + PKCE) ───────────────────────────────
# Encodes state nonce + optional code_challenge as a short-lived signed JWT.
# No DB table required — safe for serverless environments.

def create_state_token(code_challenge: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload: dict = {"purpose": "oauth_state", "exp": expire}
    if code_challenge:
        payload["cc"] = code_challenge
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_state_token(state_token: str) -> dict | None:
    """Returns payload dict on success, None if invalid/expired."""
    try:
        payload = jwt.decode(state_token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "oauth_state":
            return None
        return payload
    except JWTError:
        return None
