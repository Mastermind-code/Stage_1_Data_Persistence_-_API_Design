from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from limiter import limiter
from database import get_db
from models.user import User
from middleware.auth import require_role

router = APIRouter(prefix="/api/v1/admin")

VALID_ROLES = {"analyst", "admin"}


@router.get("/users")
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin"))
):
    users = db.query(User).order_by(User.created_at.desc()).all()
    return {
        "status": "success",
        "data": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "avatar_url": u.avatar_url,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ]
    }


@router.patch("/users/{user_id}/role")
@limiter.limit("30/minute")
async def update_user_role(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_role("admin"))
):
    body = await request.json()
    new_role = body.get("role")

    if new_role not in VALID_ROLES:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"
        })

    if user_id == current_admin.id:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "You cannot change your own role"
        })

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": "User not found"
        })

    user.role = new_role
    db.commit()
    db.refresh(user)

    return {
        "status": "success",
        "message": f"Role updated to '{new_role}'",
        "data": {"id": user.id, "username": user.username, "role": user.role}
    }


@router.patch("/users/{user_id}/status")
@limiter.limit("30/minute")
async def update_user_status(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_role("admin"))
):
    body = await request.json()
    is_active = body.get("is_active")

    if not isinstance(is_active, bool):
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "is_active must be a boolean"
        })

    if user_id == current_admin.id:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "You cannot deactivate your own account"
        })

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": "User not found"
        })

    user.is_active = is_active
    db.commit()
    db.refresh(user)

    return {
        "status": "success",
        "message": f"Account {'activated' if is_active else 'deactivated'}",
        "data": {"id": user.id, "username": user.username, "is_active": user.is_active}
    }
