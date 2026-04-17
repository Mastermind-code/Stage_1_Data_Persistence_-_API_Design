from fastapi import FastAPI
from routes.profiles import router as profiles_router
from database import engine, Base
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Profile Management API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       
    allow_credentials=False,
    allow_methods=["*"],           
    allow_headers=["*"],     
)

app.include_router(profiles_router)

@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)