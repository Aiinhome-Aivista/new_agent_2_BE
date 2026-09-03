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

class TeeLogger(object):
    def __init__(self, filename):
        self.terminal = sys.stdout if not isinstance(sys.stdout, TeeLogger) else sys.stdout.terminal
        self.filename = filename
        self.lock = threading.Lock()
        # Open in append mode 'a' so file writes always go to the end without creating sparse NULL byte gaps on Windows
        self.log = open(filename, "a", encoding="utf-8", errors="replace")

    def write(self, message):
        try:
            self.terminal.write(message)
        except Exception:
            pass
        if message:
            # Strip any accidental NULL bytes to prevent file corruption
            cleaned = message.replace("\x00", "")
            if cleaned:
                with self.lock:
                    try:
                        self.log.write(cleaned)
                        self.log.flush()
                    except Exception:
                        pass

    def flush(self):
        try:
            self.terminal.flush()
        except Exception:
            pass
        with self.lock:
            try:
                self.log.flush()
            except Exception:
                pass

# Redirect all print statements to both the terminal AND a log file safely
if not isinstance(sys.stdout, TeeLogger):
    sys.stdout = TeeLogger(os.path.join(os.path.dirname(__file__), "pipeline_trace.log"))

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
    try:
        from services.followup_scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"Failed to start followup scheduler: {e}")
        
    try:
        from init_db import run_tracker_migrations
        run_tracker_migrations()
        print("Successfully ran tracker migrations on startup.")
    except Exception as e:
        print(f"Failed to run tracker migrations: {e}")

    try:
        from services.drive_inbox_service import ensure_drive_tables
        ensure_drive_tables()
        print("Drive tables ensured on startup.")
    except Exception as e:
        print(f"Failed to ensure drive tables: {e}")

    try:
        from services.onedrive_inbox_service import ensure_onedrive_tables
        ensure_onedrive_tables()
        print("OneDrive tables ensured on startup.")
    except Exception as e:
        print(f"Failed to ensure onedrive tables: {e}")

@app.get("/")
def root():
    return {"message": "Welcome to Autonomous Contract Scope Evaluator (ACSE) API"}
