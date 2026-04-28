import os
from fastapi import Request, HTTPException, Depends, status
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from database import get_db
from models.user import User
from services.auth import verify_access_token

# 1. Extract token from either Header (CLI) or Cookie (Web)
def get_token_from_request(request: Request) -> str:
    # Try Authorization Header first (CLI)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ")[1]
    
    # Try Cookie second (Web Portal)
    token = request.cookies.get("access_token")
    if token:
        return token
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated"
    )

# 2. Verify token and fetch the actual User object
async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = get_token_from_request(request)
    payload = verify_access_token(token) # Uses your services/auth.py logic
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="Account is deactivated"
    )   
    return user

# 3. The Role Enforcement Factory
def require_role(required_role: str):
    """
    Dependency factory to check roles.
    Usage: Depends(require_role("admin"))
    """
    async def role_checker(user: User = Depends(get_current_user)):
        # Business Rule: admin role bypasses all lower checks
        if user.role != "admin" and user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{required_role}' required"
            )
        return user
    
    return role_checker
