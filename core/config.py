import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Autonomous Contract Scope Evaluator"
    APP_ENV: str = "development"
    
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api"
    
    FRONTEND_ORIGIN: str = "*"
    
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "acse_db"
    
    JWT_SECRET_KEY: str = "replace_with_secure_secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 480
    
    # ── LLM Configuration ─────────────────────────────────────────────────────
    LLM_PROVIDER: str = "gemini"  # Options: "gemini", "openai", "claude", "azure_openai", "custom"
    
    # 1. Google Gemini Settings
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    
    # 2. OpenAI Settings (ChatGPT)
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = ""  # Optional custom OpenAI-compatible endpoint
    
    # 3. Anthropic Claude Settings
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-sonnet-20241022"
    
    # 4. Azure OpenAI Settings
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4o"
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    
    # 5. Local / Custom LLM (Ollama, vLLM, FastChat)
    LLM_API_URL: str = ""
    LLM_MODEL: str = "mistral-small:24b"
    LLM_TIMEOUT: int = 300
    
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    CHROMA_PATH: str = "data/chroma_db"
    UPLOAD_PATH: str = "data/uploads"
    
    # ── Storage Configuration (local vs aws_s3) ──────────────────────────────
    STORAGE_MODE: str = "local"  # "local" for development/testing, "aws_s3" for AWS S3 bucket
    
    # ── AWS S3 Integration ────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "us-east-1"
    AWS_S3_BUCKET_NAME: str = ""
    AWS_S3_BASE_FOLDER: str = ""
    AWS_S3_AGENT_FOLDER: str = ""
    
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    
    DENSE_TOP_K: int = 10
    SPARSE_TOP_K: int = 10
    FUSION_TOP_K: int = 10
    RRF_K: int = 60
    RERANK_TOP_K: int = 8
    
    ENABLE_RERANKER: bool = True
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    
    MAX_EPISODIC_EVENTS: int = 20
    CONTEXT_COMPACTION_ENABLED: bool = True
    CONTEXT_MAX_CHARACTERS: int = 20000
    
    SMTP_SERVER: str = ""
    SMTP_PORT: int = 587
    SMTP_EMAIL: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True

    # ── Google Drive Integration ──────────────────────────────────────────────
    # Scheduler interval: runs every N hours (default 24 = once daily)
    # Set DRIVE_SYNC_INTERVAL_HOURS=0 and DRIVE_SYNC_INTERVAL_MINUTES=30 for every 30 min.
    DRIVE_SYNC_INTERVAL_HOURS: int = 24
    DRIVE_SYNC_INTERVAL_MINUTES: int = 0
    # Fernet key used to encrypt/decrypt stored credentials JSON.
    # Generate once with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    DRIVE_CREDENTIAL_ENCRYPTION_KEY: str = ""

    # ── Microsoft OneDrive Integration ────────────────────────────────────────
    ONEDRIVE_SYNC_INTERVAL_HOURS: int = 24
    ONEDRIVE_SYNC_INTERVAL_MINUTES: int = 0

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

if settings.JWT_SECRET_KEY == "replace_with_secure_secret":
    raise ValueError("CRITICAL SECURITY ERROR: JWT_SECRET_KEY is using the default value. Please set a secure secret in your environment.")
