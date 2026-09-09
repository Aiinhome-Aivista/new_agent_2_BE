# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from core.response import APIStandardResponseMiddleware
from api.routes import auth, users, projects, stakeholders, documents, baseline, monitoring, tracker, dashboard, rag, project_registers, drive, onedrive
import sys
import os

import threading

app = FastAPI(
    title=settings.APP_NAME,
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    docs_url=f"{settings.API_PREFIX}/docs",
    redoc_url=f"{settings.API_PREFIX}/redoc",
)

# Register API Response Standardizer Middleware
app.add_middleware(APIStandardResponseMiddleware)

# CORS configuration
raw_origins = [o.strip() for o in settings.FRONTEND_ORIGIN.split(",") if o.strip()] if settings.FRONTEND_ORIGIN else ["*"]
allow_origins = ["*"] if "*" in raw_origins else list(set(raw_origins + [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080"
]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
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
app.include_router(rag.router, prefix=f"{settings.API_PREFIX}/projects/{{project_id}}/rag", tags=["rag"])
app.include_router(dashboard.router, prefix=f"{settings.API_PREFIX}/dashboard", tags=["dashboard"])
app.include_router(project_registers.router, prefix=f"{settings.API_PREFIX}")
app.include_router(drive.router, prefix=f"{settings.API_PREFIX}/drive", tags=["drive"])
app.include_router(onedrive.router, prefix=f"{settings.API_PREFIX}/onedrive", tags=["onedrive"])
@app.on_event("startup")
def startup_event():
    # 1. Database connection check
    try:
        from core.database import get_db_connection
        conn = get_db_connection()
        if conn and conn.is_connected():
            cursor = conn.cursor()
            cursor.execute("SELECT DATABASE()")
            db_row = cursor.fetchone()
            db_name = db_row[0] if db_row else settings.DB_NAME
            cursor.close()
            conn.close()
            print(f"✓ Database connected successfully [{db_name}]")
        else:
            print("⚠ Database connection failed.")
    except Exception as e:
        print(f"✗ Database connection error: {e}")

    # 2. Silent initialization tasks
    try:
        from services.followup_scheduler import start_scheduler
        start_scheduler()
    except Exception:
        pass
        
    try:
        from init_db import run_tracker_migrations
        run_tracker_migrations()
    except Exception:
        pass

    try:
        from services.drive_inbox_service import ensure_drive_tables
        ensure_drive_tables()
    except Exception:
        pass

    try:
        from services.onedrive_inbox_service import ensure_onedrive_tables
        ensure_onedrive_tables()
    except Exception:
        pass

    # 3. Application Start & URL info
    api_port = getattr(settings, 'API_PORT', 8080)
    api_host = getattr(settings, 'API_HOST', '127.0.0.1')
    display_host = '127.0.0.1' if api_host in ('0.0.0.0', '') else api_host
    print(f"🚀 {settings.APP_NAME} started successfully.")
    print(f"🔗 Backend API URL: http://{display_host}:{api_port}{settings.API_PREFIX}")
    print(f"📖 Swagger Docs:    http://{display_host}:{api_port}{settings.API_PREFIX}/docs")

@app.get("/")
def root():
    return {"message": "Welcome to Autonomous Contract Scope Evaluator (ACSE) API"}
