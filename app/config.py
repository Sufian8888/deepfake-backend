from pydantic_settings import BaseSettings
from functools import lru_cache
import os
from pathlib import Path

class Settings(BaseSettings):
    # Database - PostgreSQL (with Neon as primary)
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/deepfake_db"

    # App / Frontend
    FRONTEND_URL: str = "http://localhost:3000"
    
    # CORS / External service URLs
    CORS_ORIGINS: str = "https://deepfake-detection-ovj5.onrender.com"
    MODEL_API_URL: str = "https://softwareengineer26-deepfake-model.hf.space"
    MODEL_API_URLS: str = ""
    
    # JWT Settings
    SECRET_KEY: str = "your-super-secret-key-change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Admin Credentials
    ADMIN_EMAIL: str = "admin@deepfake.com"
    ADMIN_PASSWORD: str = "admin123"
    
    # File Upload
    UPLOAD_DIR: str = str(Path(__file__).parent.parent / "uploads")
    MAX_FILE_SIZE: int = 104857600  # 100MB
    
    # Cloudinary Configuration
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Stripe Billing
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO_MONTHLY: str = ""
    STRIPE_PRICE_PRO_YEARLY: str = ""
    STRIPE_PRICE_ENTERPRISE_MONTHLY: str = ""
    STRIPE_PRICE_ENTERPRISE_YEARLY: str = ""
    GROQ_API_KEY: str = ""

    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()

settings = get_settings()
