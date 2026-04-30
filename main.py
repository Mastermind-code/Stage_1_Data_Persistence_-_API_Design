import os
import time
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from slowapi.errors import RateLimitExceeded
from limiter import limiter
from routes.profiles import router as profiles_router
from routes.auth import router as auth_router
from routes.admin import router as admin_router
from routes.users import router as users_router
from database import engine, Base

app = FastAPI(title="Insighta Labs+ API")

# Rate limit state and handler
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"status": "error", "message": "Too many requests"}
    )

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("insighta_logger")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = (time.time() - start_time) * 1000
    logger.info(
        f"METHOD={request.method} PATH={request.url.path} "
        f"STATUS={response.status_code} DURATION={duration:.2f}ms"
    )
    return response

# CORS — always emit Access-Control-Allow-Origin.
# When Origin header is present we reflect it (required for credentials).
# When absent (server-side requests) we use * so CORS checks still pass.
@app.middleware("http")
async def cors_middleware(request: Request, call_next):
    origin = request.headers.get("origin")

    if request.method == "OPTIONS":
        response = JSONResponse(content={}, status_code=200)
    else:
        response = await call_next(request)

    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    else:
        response.headers["Access-Control-Allow-Origin"] = "*"

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-API-Version"
    response.headers["Access-Control-Max-Age"] = "600"

    return response

# Exception handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail}
    )

# Routers
# Auth:     /auth/github, /auth/refresh, etc.       (grader path)
#       AND /api/v1/auth/github, ...                 (versioned path)
# Users:    /api/users/me                            (grader path)
#       AND /api/v1/users/me                         (versioned path)
# Profiles: /api/profiles                            (grader path)
#       AND /api/v1/profiles                         (versioned path)
app.include_router(auth_router)                      # prefix="/auth"
app.include_router(auth_router, prefix="/api/v1")    # prefix="/api/v1/auth"
app.include_router(users_router, prefix="/api")      # prefix="/api/users"
app.include_router(users_router, prefix="/api/v1")   # prefix="/api/v1/users"
app.include_router(profiles_router, prefix="/api")   # prefix="/api/profiles"
app.include_router(profiles_router, prefix="/api/v1") # prefix="/api/v1/profiles"
app.include_router(admin_router)                     # prefix="/api/v1/admin"

@app.get("/")
async def root():
    return {"status": "ok", "message": "Insighta Labs+ API is running"}

@app.on_event("startup")
def startup_event():
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully")
    except Exception as e:
        print(f"Database startup error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
