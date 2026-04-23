from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

from services.parser import parse_query
from database import get_db
from models.profile import Profile
from services.genderize import fetch_user_data
from services.agify import fetch_user_age
from services.nationalize import fetch_user_nationality
import asyncio
import httpx


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
router = APIRouter()

class ProfileRequest(BaseModel):
    name: str

@router.post("/api/profiles", status_code=201)
async def create_profile(request: ProfileRequest, db: Session = Depends(get_db)):
    
    # 1. Validate
    formatted_name = request.name.strip().lower()
    if not formatted_name:
        return JSONResponse(status_code=400, content={
    "status": "error",
    "message": "Missing or invalid 'name' field in request body"
})
    
    # 2. Check DB for existing profile
    existing_profile = db.query(Profile).filter(Profile.name == formatted_name).first()
    if existing_profile:
        return JSONResponse(status_code=200, content={
            "status": "success",
            "message": "Profile already exists",
            "data": serialize_profile(existing_profile)
    })

    # 3. Call all three APIs simultaneously
    try:
            results = await asyncio.gather(
                fetch_user_data(name=formatted_name),
                fetch_user_age(name=formatted_name),
                fetch_user_nationality(name=formatted_name),
            )
    except Exception as e:
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        return JSONResponse(status_code=502, content={
    "status": "error",
    "message": "An unexpected error occurred while fetching data from external services"
})
    # 4. Check for errors
    new_data = {'name': formatted_name}
    for data, error in results:
        if error:
            return JSONResponse(status_code=502, content={
                "status": "error",
                "message": error
            })
        new_data.update(data)
    # 5. Build and save profile
    try:
        new_profile = Profile(**new_data)
        db.add(new_profile)
        db.commit()
        db.refresh(new_profile)
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={
    "status": "error",
    "message": "An unexpected error occurred while saving the profile to the database"
})
    # 6. Return 201
    return JSONResponse(status_code=201, content={
    "status": "success",
    "data": serialize_profile(new_profile)
})
@router.get("/api/profiles/search")
async def search_profiles(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    filters = parse_query(q)
    
    if filters is None:
        return JSONResponse(status_code=422, content={
            "status": "error",
            "message": "Unable to interpret query"
        })

    query = db.query(Profile)

    if filters.get("gender"):
        query = query.filter(Profile.gender == filters["gender"])
    if filters.get("age_group"):
        query = query.filter(Profile.age_group == filters["age_group"])
    if filters.get("country_id"):
        query = query.filter(Profile.country_id == filters["country_id"])
    if filters.get("min_age") is not None:
        query = query.filter(Profile.age >= filters["min_age"])
    if filters.get("max_age") is not None:
        query = query.filter(Profile.age <= filters["max_age"])

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()

    return JSONResponse(status_code=200, content={
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "data": [serialize_profile(p) for p in profiles]
    })


@router.get('/api/profiles')
async def list_profiles(
    gender: Optional[str] = None, 
    country_id: Optional[str] = None, 
    age_group: Optional[str] = None, 
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    min_gender_probability: Optional[float] = None,
    min_country_probability: Optional[float] = None,
    sort_by: str = Query("created_at", regex="^(age|created_at|gender_probability)$"),
    order: str = Query("desc", regex="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1),
    db: Session = Depends(get_db)
):
    limit = min(limit, 50)
    query = db.query(Profile)

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
    if min_gender_probability is not None:
        query = query.filter(Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(Profile.country_probability >= min_country_probability)

    sort_column = getattr(Profile, sort_by)
    if order == "desc":
        sort_column = sort_column.desc()
    query = query.order_by(sort_column)

    total_profiles = query.count()
    query = query.offset((page - 1) * limit).limit(limit)

    profiles = query.all()
    return JSONResponse(status_code=200, content={
        "status": "success", 
        'total': total_profiles,
        'limit': limit,
        "page": page,
        "data": [serialize_profile(profile) for profile in profiles]
    })


@router.get('/api/profiles/{profile_id}')
async def get_profile(profile_id: str, db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()

    if not profile:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": "Profile not found"
        })
    return JSONResponse(status_code=200, content={
        "status": "success",
        "data": serialize_profile(profile)
    })



@router.delete('/api/profiles/{profile_id}')
async def delete_profile(profile_id: str, db: Session = Depends(get_db)):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()

    if not profile:
        return JSONResponse(status_code=404, content={
            "status": "error",
            "message": "Profile not found"
        })
    
    try:
        db.delete(profile)
        db.commit()
    except Exception as e:
        db.rollback()
        return JSONResponse(status_code=500, content={
    "status": "error",
    "message": "An unexpected error occurred while deleting the profile from the database"
})
    return Response(status_code=204)