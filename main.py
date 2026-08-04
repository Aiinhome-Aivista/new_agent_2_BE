# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from core.response import APIStandardResponseMiddleware
from core.risk_config_tables import create_risk_config_tables
from api.routes import auth, users, projects, stakeholders, documents, baseline, monitoring, tracker, dashboard, rag

# Initialize config tables on startup (idempotent — CREATE TABLE IF NOT EXISTS)
create_risk_config_tables()

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

@app.on_event("startup")
def startup_event():
    try:
        from services.followup_scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"Failed to start followup scheduler: {e}")

@app.get("/")
def root():
    return {"message": "Welcome to Autonomous Contract Scope Evaluator (ACSE) API"}

@app.on_event("startup")
def run_migrations_started_at():
    print("Running database migration for started_at column...")
    from core.database import get_db_connection
    conn = get_db_connection()
    if not conn:
        print("Migration: Failed to connect.")
        return
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM documents LIKE 'processing_started_at'")
        col_exists = cursor.fetchone()
        if not col_exists:
            print("Migration: Adding 'processing_started_at' column...")
            cursor.execute("ALTER TABLE documents ADD COLUMN processing_started_at TIMESTAMP NULL DEFAULT NULL")
            conn.commit()
            print("Migration: Column added.")
        else:
            print("Migration: Column already exists.")
            
        # Clear stuck processing states
        print("Migration: Resetting stuck PROCESSING documents...")
        cursor.execute("UPDATE documents SET processing_status = 'FAILED', processing_error = 'Server restarted or process crashed', processing_progress = 0, processing_step = 'Failed' WHERE processing_status = 'PROCESSING'")
        conn.commit()
        print("Migration: Reset complete.")
        
        cursor.close()
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn.close()



