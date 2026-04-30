from fastapi import APIRouter, Depends, Request
from limiter import limiter
from models.user import User
from middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1/users")


@router.get("/me")
@limiter.limit("60/minute")
async def get_me(request: Request, current_user: User = Depends(get_current_user)):
    """Returns the currently authenticated user's profile."""
    return {
        "status": "success",
        "data": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
            "is_active": current_user.is_active,
        }
    }
