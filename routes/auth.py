import os
import httpx
from fastapi import APIRouter, Depends,  Query, Response, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Optional
import secrets
from database import get_db
from models.user import User
from models.token import RefreshToken
from services.auth import create_access_token, create_refresh_token

router = APIRouter(prefix="/api/v1/auth")

CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
WEB_PORTAL_URL = os.getenv("WEB_PORTAL_URL", "http://localhost:3000")

@router.get("/github")
async def github_login(code_challenge: Optional[str] = None):
    """
    Redirects to GitHub. CLI sends code_challenge; Web Browser does not.
    """
    state = secrets.token_urlsafe(16)
    url = f"https://github.com/login/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope=user:email"
    if code_challenge:
        url += f"&code_challenge={code_challenge}&code_challenge_method=S256"
    return RedirectResponse(url=url)

@router.get("/github/callback")
async def github_callback(
    code: str, 
    code_verifier: Optional[str] = None, 
    db: Session = Depends(get_db)
):
    # 1. Exchange Code for GitHub Token
    async with httpx.AsyncClient() as client:
        payload = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier # PKCE for CLI

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

        # 2. Get User Details
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_data['access_token']}"}
        )
        gh_user = user_res.json()

    # 3. Upsert User (Sync with DB)
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
        user.last_login_at = datetime.now(timezone.utc)
        user.avatar_url = gh_user.get("avatar_url")    

    db.commit()
    db.refresh(user)

    # Check is_active AFTER upsert
    if not user.is_active:
        return JSONResponse(status_code=403, content={
            "status": "error",
            "message": "Account is deactivated"
        })
    # 4. Generate Insighta Tokens
    access_token = create_access_token(user.id, user.role)
    refresh_token_str = create_refresh_token()

    # 5. Persist Refresh Token
    new_refresh = RefreshToken(
        user_id=user.id,
        token=refresh_token_str,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    db.add(new_refresh)
    db.commit()

    # 6. Response Strategy
    if code_verifier:
        # CLI Scenario: Return JSON
        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token_str
        }
    else:
        # Web Browser Scenario: Set Cookie & Redirect
        response = RedirectResponse(url=f"{WEB_PORTAL_URL}/dashboard")
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True, # Set to False for local dev without HTTPS
            samesite="lax",
            max_age=180 # 3 mins
        )
        return response

@router.post("/refresh")
async def refresh_token(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    token_str = body.get("refresh_token")
    
    # Find token in DB
    token = db.query(RefreshToken).filter(
        RefreshToken.token == token_str,
        RefreshToken.is_revoked == False
    ).first()
    
    if not token or token.expires_at < datetime.now(timezone.utc):
        return JSONResponse(status_code=401, content={
            "status": "error",
            "message": "Invalid or expired refresh token"
        })
    
    # Revoke old token
    token.is_revoked = True
    db.commit()
    
    # Issue new pair
    user = db.query(User).filter(User.id == token.user_id).first()
    new_access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token()
    
    db.add(RefreshToken(
        user_id=user.id,
        token=new_refresh,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
    ))
    db.commit()
    
    return {
        "status": "success",
        "access_token": new_access,
        "refresh_token": new_refresh
    }

@router.post("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    token_str = body.get("refresh_token")
    
    token = db.query(RefreshToken).filter(
        RefreshToken.token == token_str
    ).first()
    
    if token:
        token.is_revoked = True
        db.commit()
    
    return JSONResponse(status_code=200, content={
        "status": "success",
        "message": "Logged out successfully"
    })