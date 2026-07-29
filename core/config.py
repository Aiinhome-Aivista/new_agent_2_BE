import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Autonomous Contract Scope Evaluator"
    APP_ENV: str = "development"
    
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api"
    
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    
    DB_HOST: str = "localhost"
    DB_PORT: int = 3306
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    DB_NAME: str = "acse_db"
    
    JWT_SECRET_KEY: str = "replace_with_secure_secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 480
    
    LLM_API_URL: str
    LLM_MODEL: str
    LLM_TIMEOUT: int = 3600
    
    USE_GEMINI: bool = False
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    CHROMA_PATH: str = "data/chroma_db"
    UPLOAD_PATH: str = "data/uploads"
    
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

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
