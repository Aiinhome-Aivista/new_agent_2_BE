# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from core.response import APIStandardResponseMiddleware
from api.routes import auth, users, projects, stakeholders, documents, baseline, monitoring, tracker, dashboard
app = FastAPI(
    title=settings.APP_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    docs_url=f"{settings.API_PREFIX}/docs",
    redoc_url=f"{settings.API_PREFIX}/redoc",
)

# Register API Response Standardizer Middleware
app.add_middleware(APIStandardResponseMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.FRONTEND_ORIGIN,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix=f"{settings.API_PREFIX}/auth", tags=["auth"])
app.include_router(users.router, prefix=f"{settings.API_PREFIX}/users", tags=["users"])
app.include_router(projects.router, prefix=f"{settings.API_PREFIX}/projects", tags=["projects"])
app.include_router(stakeholders.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/stakeholders", tags=["stakeholders"])
app.include_router(documents.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/documents", tags=["documents"])
app.include_router(baseline.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/baseline", tags=["baseline"])
app.include_router(monitoring.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/monitoring", tags=["monitoring"])
app.include_router(tracker.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/tracker", tags=["tracker"])
app.include_router(dashboard.router, prefix=f"{settings.API_PREFIX}/dashboard", tags=["dashboard"])

@app.get("/")
def root():
    return {"message": "Welcome to Autonomous Contract Scope Evaluator (ACSE) API"}
