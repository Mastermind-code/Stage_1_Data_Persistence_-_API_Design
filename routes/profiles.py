from datetime import datetime, timezone
from typing import Optional
import io
import csv
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc
from pydantic import BaseModel
import asyncio
from limiter import limiter
from database import get_db
from models.profile import Profile
from services.parser import parse_query
from services.genderize import fetch_user_data
from services.agify import fetch_user_age
from services.nationalize import fetch_user_nationality
from middleware.auth import get_current_user, require_role
from fastapi import Header



def verify_api_version(x_api_version: str = Header(None)):
    if x_api_version != "1":
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "API version header required"
        })
    
router = APIRouter(prefix="/api/v1/profiles", dependencies=[Depends(verify_api_version)])

class ProfileRequest(BaseModel):
    name: str

def serialize_profile(profile):
    return {
        "id": profile.id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": profile.gender_probability,
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": profile.country_probability,
        "created_at": profile.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

def apply_filters(query, gender, country_id, age_group, min_age, max_age, min_gender_prob, min_country_prob):
    if gender:
        query = query.filter(Profile.gender == gender.lower())
    if country_id:
        query = query.filter(Profile.country_id == country_id.upper())
    if age_group:
        query = query.filter(Profile.age_group == age_group.lower())
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    if min_gender_prob is not None:
        query = query.filter(Profile.gender_probability >= min_gender_prob)
    if min_country_prob is not None:
        query = query.filter(Profile.country_probability >= min_country_prob)
    return query

def get_paginated_data(query, page, limit, url_path="/api/v1/profiles"):
    total = query.count()
    total_pages = (total + limit - 1) // limit
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    
    links = {
        "self": f"{url_path}?page={page}&limit={limit}",
        "next": f"{url_path}?page={page+1}&limit={limit}" if page < total_pages else None,
        "prev": f"{url_path}?page={page-1}&limit={limit}" if page > 1 else None
    }
    
    return {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": links,
        "data": [serialize_profile(p) for p in profiles]
    }

@router.post("", status_code=201, dependencies=[Depends(require_role("admin"))])
@limiter.limit("60/minute")
async def create_profile(request: Request, profile_data: ProfileRequest, db: Session = Depends(get_db)):
    formatted_name = profile_data.name.strip().lower()
    if not formatted_name:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid name"})
    
    existing = db.query(Profile).filter(Profile.name == formatted_name).first()
    if existing:
        return {"status": "success", "message": "Profile already exists", "data": serialize_profile(existing)}

    try:
        results = await asyncio.gather(
            fetch_user_data(formatted_name),
            fetch_user_age(formatted_name),
            fetch_user_nationality(formatted_name)
        )
        new_data = {"name": formatted_name}
        for data, error in results:
            if error: return JSONResponse(status_code=502, content={"status": "error", "message": error})
            new_data.update(data)
        
        profile = Profile(**new_data)
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return {"status": "success", "data": serialize_profile(profile)}
    except Exception:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Server error"})

@router.get("", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def list_profiles(
    request: Request,
    gender: Optional[str] = None, country_id: Optional[str] = None, age_group: Optional[str] = None,
    min_age: Optional[int] = None, max_age: Optional[int] = None,
    min_gender_probability: Optional[float] = None, min_country_probability: Optional[float] = None,
    sort_by: str = Query("created_at", regex="^(age|created_at|gender_probability)$"),
    order: str = Query("desc", regex="^(asc|desc)$"),
    page: int = Query(1, ge=1), limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    query = db.query(Profile)
    query = apply_filters(query, gender, country_id, age_group, min_age, max_age, min_gender_probability, min_country_probability)
    
    sort_col = getattr(Profile, sort_by)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    
    return get_paginated_data(query, page, limit)

@router.get("/search", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def search_profiles(request: Request, q: str = Query(..., min_length=1), page: int = Query(1, ge=1), limit: int = Query(10, ge=1, le=50), db: Session = Depends(get_db)):
    filters = parse_query(q)
    if not filters:
        return JSONResponse(status_code=422, content={"status": "error", "message": "Unable to interpret query"})
    
    query = db.query(Profile)
    # Mapping parser keys to apply_filters parameters
    query = apply_filters(
        query, 
        filters.get("gender"), filters.get("country_id"), filters.get("age_group"),
        filters.get("min_age"), filters.get("max_age"), None, None
    )
    
    return get_paginated_data(query, page, limit, url_path="/api/v1/profiles/search")

@router.get("/export", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def export_profiles(
    request: Request,
    gender: Optional[str] = None,
    country_id: Optional[str] = None,
    age_group: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    sort_by: str = Query("created_at", regex="^(age|created_at|gender_probability)$"),
    order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db)
):
    query = db.query(Profile)
    query = apply_filters(query, gender, country_id, age_group, min_age, max_age, None, None)
    sort_col = getattr(Profile, sort_by)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    profiles = query.all()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "gender", "gender_probability", "age", 
                     "age_group", "country_id", "country_name", 
                     "country_probability", "created_at"])
    for p in profiles:
        writer.writerow([p.id, p.name, p.gender, p.gender_probability, 
                        p.age, p.age_group, p.country_id, p.country_name,
                        p.country_probability, p.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=profiles_{timestamp}.csv"}
    )

@router.get("/{profile_id}", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def get_profile(request: Request, profile_id: str, db: Session = Depends(get_db)):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p: return JSONResponse(status_code=404, content={"status": "error", "message": "Profile not found"})
    return {"status": "success", "data": serialize_profile(p)}

@router.delete("/{profile_id}", dependencies=[Depends(require_role("admin"))])
@limiter.limit("60/minute")
async def delete_profile(request: Request, profile_id: str, db: Session = Depends(get_db)):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p: return JSONResponse(status_code=404, content={"status": "error", "message": "Profile not found"})
    db.delete(p)
    db.commit()
    return Response(status_code=204)
