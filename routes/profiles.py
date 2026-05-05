import asyncio
import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from limiter import limiter
from middleware.auth import get_current_user, require_role
from models.profile import Profile
from pydantic import BaseModel
from services.agify import fetch_user_age
from services.cache import (
    delete_by_prefix,
    get_cached,
    make_count_key,
    make_list_key,
    make_profile_key,
    make_search_key,
    set_cached,
)
from services.genderize import fetch_user_data
from services.nationalize import fetch_user_nationality
from services.parser import parse_query
from sqlalchemy.orm import Session

router = APIRouter(prefix="/profiles")


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
        "created_at": profile.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def apply_filters(
    query,
    gender,
    country_id,
    age_group,
    min_age,
    max_age,
    min_gender_prob,
    min_country_prob,
):
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


def get_paginated_data(
    query, page, limit, url_path="/api/v1/profiles", count_cache_key=None
):
    if count_cache_key is not None:
        cached_count = get_cached(count_cache_key)
        if cached_count is not None:
            total = int(cached_count)
        else:
            total = query.count()
            set_cached(count_cache_key, str(total), ttl_seconds=1800)
    else:
        total = query.count()

    total_pages = (total + limit - 1) // limit
    profiles = query.offset((page - 1) * limit).limit(limit).all()

    links = {
        "self": f"{url_path}?page={page}&limit={limit}",
        "next": f"{url_path}?page={page + 1}&limit={limit}"
        if page < total_pages
        else None,
        "prev": f"{url_path}?page={page - 1}&limit={limit}" if page > 1 else None,
    }

    return {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": links,
        "data": [serialize_profile(p) for p in profiles],
    }


@router.post("", status_code=201, dependencies=[Depends(require_role("admin"))])
@limiter.limit("60/minute")
async def create_profile(
    request: Request, profile_data: ProfileRequest, db: Session = Depends(get_db)
):
    formatted_name = profile_data.name.strip().lower()
    if not formatted_name:
        return JSONResponse(
            status_code=400, content={"status": "error", "message": "Invalid name"}
        )

    existing = db.query(Profile).filter(Profile.name == formatted_name).first()
    if existing:
        return {
            "status": "success",
            "message": "Profile already exists",
            "data": serialize_profile(existing),
        }

    try:
        results = await asyncio.gather(
            fetch_user_data(formatted_name),
            fetch_user_age(formatted_name),
            fetch_user_nationality(formatted_name),
        )
        new_data = {"name": formatted_name}
        for data, error in results:
            if error:
                return JSONResponse(
                    status_code=502, content={"status": "error", "message": error}
                )
            new_data.update(data)

        profile = Profile(**new_data)
        db.add(profile)
        db.commit()
        delete_by_prefix("profiles:list:")
        delete_by_prefix("profiles:search:")
        delete_by_prefix("profiles:count:")
        db.refresh(profile)
        return {"status": "success", "data": serialize_profile(profile)}
    except Exception:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": "Server error"}
        )


@router.get("", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def list_profiles(
    request: Request,
    gender: Optional[str] = None,
    country_id: Optional[str] = None,
    age_group: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    min_gender_probability: Optional[float] = None,
    min_country_probability: Optional[float] = None,
    sort_by: str = Query("created_at", pattern="^(age|created_at|gender_probability)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    # Build and check cache key BEFORE touching the database.
    # All params that affect the result set must be included — including the
    # probability filters which were previously missing.
    list_params = {
        "gender": gender,
        "country_id": country_id,
        "age_group": age_group,
        "min_age": min_age,
        "max_age": max_age,
        "min_gender_probability": min_gender_probability,
        "min_country_probability": min_country_probability,
        "sort_by": sort_by,
        "order": order,
        "page": page,
        "limit": limit,
    }
    cache_key = make_list_key(list_params)
    cached = get_cached(cache_key)
    if cached:
        return json.loads(cached)

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender,
        country_id,
        age_group,
        min_age,
        max_age,
        min_gender_probability,
        min_country_probability,
    )
    sort_col = getattr(Profile, sort_by)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    count_key = make_count_key(list_params)
    result = get_paginated_data(query, page, limit, count_cache_key=count_key)
    set_cached(cache_key, json.dumps(result), ttl_seconds=300)
    return result


@router.get("/search", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def search_profiles(
    request: Request,
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    filters = parse_query(q)
    if not filters:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Unable to interpret query"},
        )

    # Check cache before building or executing any DB query.
    # parse_query already returns a normalised dict, so make_search_key
    # produces an identical key for logically equivalent queries.
    cache_key = make_search_key(filters)
    cached = get_cached(cache_key)
    if cached:
        return json.loads(cached)

    query = db.query(Profile)
    query = apply_filters(
        query,
        filters.get("gender"),
        filters.get("country_id"),
        filters.get("age_group"),
        filters.get("min_age"),
        filters.get("max_age"),
        None,
        None,
    )

    result = get_paginated_data(query, page, limit, url_path="/api/v1/profiles/search")
    set_cached(cache_key, json.dumps(result), ttl_seconds=300)
    return result


@router.get("/export", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def export_profiles(
    request: Request,
    gender: Optional[str] = None,
    country_id: Optional[str] = None,
    age_group: Optional[str] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    sort_by: str = Query("created_at", pattern="^(age|created_at|gender_probability)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    query = db.query(Profile)
    query = apply_filters(
        query, gender, country_id, age_group, min_age, max_age, None, None
    )
    sort_col = getattr(Profile, sort_by)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    profiles = query.all()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "name",
            "gender",
            "gender_probability",
            "age",
            "age_group",
            "country_id",
            "country_name",
            "country_probability",
            "created_at",
        ]
    )
    for p in profiles:
        writer.writerow(
            [
                p.id,
                p.name,
                p.gender,
                p.gender_probability,
                p.age,
                p.age_group,
                p.country_id,
                p.country_name,
                p.country_probability,
                p.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ]
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=profiles_{timestamp}.csv"
        },
    )


@router.get("/{profile_id}", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def get_profile(request: Request, profile_id: str, db: Session = Depends(get_db)):
    cache_key = make_profile_key(profile_id)
    cached = get_cached(cache_key)
    if cached:
        return json.loads(cached)

    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        return JSONResponse(
            status_code=404, content={"status": "error", "message": "Profile not found"}
        )

    result = {"status": "success", "data": serialize_profile(p)}
    set_cached(cache_key, json.dumps(result), ttl_seconds=600)
    return result


@router.delete("/{profile_id}", dependencies=[Depends(require_role("admin"))])
@limiter.limit("60/minute")
async def delete_profile(
    request: Request, profile_id: str, db: Session = Depends(get_db)
):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        return JSONResponse(
            status_code=404, content={"status": "error", "message": "Profile not found"}
        )
    db.delete(p)
    db.commit()
    delete_by_prefix("profiles:list:")
    delete_by_prefix("profiles:search:")
    delete_by_prefix("profiles:count:")
    delete_by_prefix(f"profiles:get:{profile_id}")
    return Response(status_code=204)
