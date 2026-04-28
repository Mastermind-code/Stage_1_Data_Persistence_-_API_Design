import os
import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from routes.profiles import router as profiles_router
from routes.auth import router as auth_router # Added Auth Router
from database import engine, Base   

# 1. Initialize Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Insighta Labs+ API")

# Setup Rate Limit State and Handler
app.state.limiter = limiter
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"status": "error", "message": "Too many requests"}

# 2. Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("insighta_logger")

# 3. Custom Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = (time.time() - start_time) * 1000  # Convert to ms
    logger.info(
        f"METHOD={request.method} PATH={request.url.path} "
        f"STATUS={response.status_code} DURATION={duration:.2f}ms"
    )
    return response

# 4. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        os.getenv("BACKEND_URL", "http://localhost:8000")
    ],      
    allow_credentials=True, # Changed to True for HTTP-only cookies
    allow_methods=["*"],           
    allow_headers=["*"],     
)

# 5. Exception Handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": exc.detail}
    )

# 6. Register Routers
app.include_router(profiles_router)
app.include_router(auth_router)

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
