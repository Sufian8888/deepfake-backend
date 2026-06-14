from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import logging
import time
from starlette.requests import Request

from app.init_db import init_db
from app.routes import auth, upload, predictions, dashboard, admin, billing, contact, faq_chat, chatbot
from app.config import settings


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Starting DeepFake Detection API...")
    print(f"📝 API Documentation: http://localhost:8000/docs")
    try:
        init_db()
        print("✅ Database initialized successfully")
    except Exception as e:
        print(f"⚠️  Database initialization warning: {str(e)}")
        logger.error(f"Database init error: {str(e)}")
    yield
    # Shutdown
    print("👋 Shutting down...")

app = FastAPI(
    title="DeepFake Detection API",
    description="Backend API for video and audio deepfake detection",
    version="1.0.0",
    lifespan=lifespan
)


@app.middleware("http")
async def log_request_duration(request: Request, call_next):
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info("%s %s -> %s in %.1fms", request.method, request.url.path, response.status_code, elapsed_ms)
    return response

cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
allowed_origins = {origin.strip() for origin in cors_origins.split(",") if origin.strip()}

frontend_url = settings.FRONTEND_URL.rstrip("/")
if frontend_url:
    allowed_origins.add(frontend_url)
    if frontend_url.startswith("http://localhost:"):
        allowed_origins.add(frontend_url.replace("http://localhost:", "http://127.0.0.1:"))
    elif frontend_url.startswith("http://127.0.0.1:"):
        allowed_origins.add(frontend_url.replace("http://127.0.0.1:", "http://localhost:"))

allowed_origins = sorted(allowed_origins)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(upload.router, prefix="/api/upload", tags=["Upload"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["Predictions"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(faq_chat.router, prefix="/api/faq-chat", tags=["FAQ Chat"])
app.include_router(chatbot.router, prefix="/api/chatbot", tags=["Chatbot"])
app.include_router(contact.router, prefix="/api", tags=["Contact"])


os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
try:
    app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")
except Exception as e:
    logger.warning(f"Could not mount uploads directory: {e}")

@app.get("/")
async def root():
    return {
        "message": "DeepFake Detection API",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
