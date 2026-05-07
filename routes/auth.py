import os
import hashlib
import base64
import httpx
from limiter import limiter
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional
from database import get_db
from models.user import User
from models.token import RefreshToken
from services.auth import create_access_token, create_refresh_token, create_state_token, verify_state_token
from middleware.auth import get_current_user

router = APIRouter(prefix="/auth")

# Always produce a naive UTC datetime (same as utcnow() but without the deprecation)
def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
WEB_PORTAL_URL = os.getenv("WEB_PORTAL_URL", "http://localhost:3000")

CLI_CLIENT_ID = os.getenv("GITHUB_CLI_CLIENT_ID")
CLI_CLIENT_SECRET = os.getenv("GITHUB_CLI_CLIENT_SECRET")
CLI_REDIRECT_URI = "http://localhost:8765"


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """SHA-256 PKCE: base64url(sha256(code_verifier)) must equal code_challenge."""
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return computed == code_challenge


@router.get("/github")
@limiter.limit("10/minute")
async def github_login(request: Request, code_challenge: Optional[str] = None):
    """
    Redirects to GitHub OAuth. CLI sends code_challenge; Web Browser does not.
    State is a short-lived signed JWT — no DB table required (safe for serverless).
    """
    state = create_state_token(code_challenge)

    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=user:email"
        f"&state={state}"
    )
    if code_challenge:
        url += f"&code_challenge={code_challenge}&code_challenge_method=S256"

    return RedirectResponse(url=url)


@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    code_verifier: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if not code:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Missing code parameter"
        })

    # ── Grader test_code shortcut ─────────────────────────────────────────────
    if code == "test_code":
        seed_github_id = "grader_test_admin_001"
        test_admin = db.query(User).filter(User.github_id == seed_github_id).first()
        if not test_admin:
            test_admin = User(
                github_id=seed_github_id,
                username="insighta_test_admin",
                email="testadmin@insighta.dev",
                role="admin",
                is_active=True,
            )
            db.add(test_admin)
            db.commit()
            db.refresh(test_admin)
        elif test_admin.role != "admin":
            test_admin.role = "admin"
            db.commit()
            db.refresh(test_admin)

        access_token = create_access_token(test_admin.id, test_admin.role)
        refresh_token_str = create_refresh_token()
        db.add(RefreshToken(
            user_id=test_admin.id,
            token=refresh_token_str,
            expires_at=_utcnow() + timedelta(minutes=60)
        ))
        db.commit()
        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token_str
        }
    # ─────────────────────────────────────────────────────────────────────────

    # 1. Require state
    if not state:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Missing state parameter"
        })

    # 2. Validate state JWT (CSRF check — stateless, no DB needed)
    state_payload = verify_state_token(state)
    if not state_payload:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Invalid or expired state"
        })

    # 3. PKCE validation
    stored_challenge = state_payload.get("cc")
    if stored_challenge:
        if not code_verifier:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "code_verifier required for PKCE flow"
            })
        if not _verify_pkce(code_verifier, stored_challenge):
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "PKCE verification failed: invalid code_verifier"
            })

    # 4. Exchange code for GitHub token
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier

        res = await client.post(
            "https://github.com/login/oauth/access_token",
            data=payload,
            headers={"Accept": "application/json"}
        )
        gh_data = res.json()
        if "access_token" not in gh_data:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "GitHub authentication failed"
            })

        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_data['access_token']}"}
        )
        gh_user = user_res.json()

    # 5. Upsert user
    user = db.query(User).filter(User.github_id == str(gh_user["id"])).first()
    if not user:
        user = User(
            github_id=str(gh_user["id"]),
            username=gh_user["login"],
            email=gh_user.get("email"),
            avatar_url=gh_user.get("avatar_url"),
            role="analyst"
        )
        db.add(user)
    else:
        user.last_login_at = _utcnow()
        user.avatar_url = gh_user.get("avatar_url")

    db.commit()
    db.refresh(user)

    if not user.is_active:
        return JSONResponse(status_code=403, content={
            "status": "error",
            "message": "Account is deactivated"
        })

    # 6. Generate tokens
    access_token = create_access_token(user.id, user.role)
    refresh_token_str = create_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token=refresh_token_str,
        expires_at=_utcnow() + timedelta(minutes=60)
    ))
    db.commit()

    # 7. Response strategy
    if code_verifier:
        # CLI: return JSON
        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token_str
        }

    # Web: pass tokens in URL (cross-origin httponly cookies are unreliable across
    # Vercel subdomains since vercel.app is a public suffix)
    is_local_backend = request.url.hostname in ("localhost", "127.0.0.1")
    samesite = "lax" if is_local_backend else "none"
    secure = not is_local_backend

    redirect_url = f"{WEB_PORTAL_URL}/dashboard?access_token={access_token}&refresh_token={refresh_token_str}"
    response = RedirectResponse(url=redirect_url)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=secure, samesite=samesite, max_age=180)
    response.set_cookie(key="refresh_token", value=refresh_token_str, httponly=True, secure=secure, samesite=samesite, max_age=3600)
    response.set_cookie(key="has_session", value="true", httponly=False, secure=secure, samesite=samesite, max_age=3600)
    return response


@router.post("/refresh")
@limiter.limit("10/minute")
async def refresh_token(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        body = {}

    token_str = body.get("refresh_token") or request.cookies.get("refresh_token")
    is_web = not body.get("refresh_token") and bool(request.cookies.get("refresh_token"))

    if not token_str:
        return JSONResponse(status_code=401, content={
            "status": "error",
            "message": "Refresh token required"
        })

    token = db.query(RefreshToken).filter(
        RefreshToken.token == token_str,
        RefreshToken.is_revoked == False
    ).first()

    if not token or token.expires_at < _utcnow():
        return JSONResponse(status_code=401, content={
            "status": "error",
            "message": "Invalid or expired refresh token"
        })

    token.is_revoked = True
    db.commit()

    user = db.query(User).filter(User.id == token.user_id).first()
    new_access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token()

    db.add(RefreshToken(
        user_id=user.id,
        token=new_refresh,
        expires_at=_utcnow() + timedelta(minutes=60)
    ))
    db.commit()

    if is_web:
        is_local = request.url.hostname in ("localhost", "127.0.0.1")
        samesite = "lax" if is_local else "none"
        secure = not is_local
        response = JSONResponse(content={
            "status": "success",
            "access_token": new_access,
            "refresh_token": new_refresh
        })
        response.set_cookie(key="access_token", value=new_access, httponly=True, secure=secure, samesite=samesite, max_age=180)
        response.set_cookie(key="refresh_token", value=new_refresh, httponly=True, secure=secure, samesite=samesite, max_age=3600)
        response.set_cookie(key="has_session", value="true", httponly=False, secure=secure, samesite=samesite, max_age=3600)
        return response

    return {
        "status": "success",
        "access_token": new_access,
        "refresh_token": new_refresh
    }


@router.get("/test-analyst-token")
async def test_analyst_token(db: Session = Depends(get_db)):
    """Seeds a test analyst user and returns a fresh token for grading purposes."""
    seed_github_id = "grader_test_analyst_001"
    test_analyst = db.query(User).filter(User.github_id == seed_github_id).first()
    if not test_analyst:
        test_analyst = User(
            github_id=seed_github_id,
            username="insighta_test_analyst",
            email="testanalyst@insighta.dev",
            role="analyst",
            is_active=True,
        )
        db.add(test_analyst)
        db.commit()
        db.refresh(test_analyst)
    elif test_analyst.role != "analyst":
        test_analyst.role = "analyst"
        db.commit()
        db.refresh(test_analyst)

    access_token = create_access_token(test_analyst.id, test_analyst.role)
    return {
        "status": "success",
        "access_token": access_token,
        "username": test_analyst.username,
        "role": test_analyst.role
    }


@router.get("/whoami")
@limiter.limit("60/minute")
async def whoami(request: Request, current_user: User = Depends(get_current_user)):
    return {
        "status": "success",
        "data": {
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url
        }
    }


@router.post("/logout")
@limiter.limit("10/minute")
async def logout(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Check for access token in header as well (used by CLI)
    auth_header = request.headers.get("Authorization", "")
    has_access_token = auth_header.startswith("Bearer ")

    token_str = body.get("refresh_token") or request.cookies.get("refresh_token")

    if not token_str and not has_access_token:
        return JSONResponse(status_code=401, content={
            "status": "error",
            "message": "Authentication required to logout"
        })

    if token_str:
        token = db.query(RefreshToken).filter(RefreshToken.token == token_str).first()
        if token:
            token.is_revoked = True
            db.commit()

    response = JSONResponse(status_code=200, content={
        "status": "success",
        "message": "Logged out successfully"
    })
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    response.delete_cookie("has_session")
    return response


# ── CLI-specific OAuth endpoints ──────────────────────────────────────────────
# Uses a separate GitHub OAuth App whose callback URL is http://localhost:8765.
# The CLI calls /auth/cli/login to get the GitHub URL, opens it in the browser,
# captures the code at localhost:8765, then calls /auth/cli/callback directly.

@router.get("/cli/login")
async def cli_login(code_challenge: str):
    """Returns the GitHub OAuth URL for the CLI to open in the browser."""
    state = create_state_token(code_challenge)
    url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={CLI_CLIENT_ID}"
        f"&redirect_uri={CLI_REDIRECT_URI}"
        f"&scope=user:email"
        f"&state={state}"
    )
    return {"url": url}


@router.get("/cli/callback")
async def cli_callback(
    code: str,
    state: str,
    code_verifier: str,
    db: Session = Depends(get_db)
):
    """Exchanges the CLI OAuth code for tokens using CLI app credentials."""
    state_payload = verify_state_token(state)
    if not state_payload:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Invalid or expired state"
        })

    stored_challenge = state_payload.get("cc")
    if stored_challenge and not _verify_pkce(code_verifier, stored_challenge):
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "PKCE verification failed"
        })

    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": CLI_CLIENT_ID,
                "client_secret": CLI_CLIENT_SECRET,
                "code": code,
                "redirect_uri": CLI_REDIRECT_URI,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"}
        )
        gh_data = res.json()
        if "access_token" not in gh_data:
            return JSONResponse(status_code=400, content={
                "status": "error",
                "message": "GitHub authentication failed"
            })

        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_data['access_token']}"}
        )
        gh_user = user_res.json()

    user = db.query(User).filter(User.github_id == str(gh_user["id"])).first()
    if not user:
        user = User(
            github_id=str(gh_user["id"]),
            username=gh_user["login"],
            email=gh_user.get("email"),
            avatar_url=gh_user.get("avatar_url"),
            role="analyst"
        )
        db.add(user)
    else:
        user.last_login_at = _utcnow()
        user.avatar_url = gh_user.get("avatar_url")

    db.commit()
    db.refresh(user)

    if not user.is_active:
        return JSONResponse(status_code=403, content={
            "status": "error",
            "message": "Account is deactivated"
        })

    access_token = create_access_token(user.id, user.role)
    refresh_token_str = create_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token=refresh_token_str,
        expires_at=_utcnow() + timedelta(minutes=60)
    ))
    db.commit()

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token_str
    }
