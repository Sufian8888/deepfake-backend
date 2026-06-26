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

is_production = not (
    "localhost" in settings.FRONTEND_URL or "127.0.0.1" in settings.FRONTEND_URL
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Starting DeepFake Detection API...")
    if is_production:
        print("🔒 Production mode — API docs disabled")
        if settings.SECRET_KEY == "your-super-secret-key-change-this-in-production":
            logger.error("SECRET_KEY is still the default value in production!")
    else:
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
    lifespan=lifespan,
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def log_request_duration(request: Request, call_next):
    started_at = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    logger.info("%s %s -> %s in %.1fms", request.method, request.url.path, response.status_code, elapsed_ms)
    return response

cors_origins = settings.CORS_ORIGINS
canonical_origins = settings.CANONICAL_FRONTEND_ORIGINS


def parse_cors_origins(raw: str) -> set[str]:
    origins: set[str] = set()
    for entry in raw.split(","):
        origin = entry.strip().strip('"').strip("'").rstrip("/")
        if origin:
            origins.add(origin)
    return origins


allowed_origins = parse_cors_origins(cors_origins)
allowed_origins.update(parse_cors_origins(canonical_origins))

frontend_url = settings.FRONTEND_URL.strip().strip('"').strip("'").rstrip("/")
if frontend_url:
    allowed_origins.add(frontend_url)
    if frontend_url.startswith("http://localhost:"):
        allowed_origins.add(frontend_url.replace("http://localhost:", "http://127.0.0.1:"))
    elif frontend_url.startswith("http://127.0.0.1:"):
        allowed_origins.add(frontend_url.replace("http://127.0.0.1:", "http://localhost:"))
    elif frontend_url.startswith("https://") and not frontend_url.startswith("https://www."):
        allowed_origins.add(f"https://www.{frontend_url[len('https://'):]}")

allowed_origins = sorted(allowed_origins)
logger.info("CORS allowed origins: %s", ", ".join(allowed_origins))

# Allow LAN/private IPs during local dev (e.g. http://192.168.x.x:3000 from `next dev`)
is_local_dev = not is_production
local_dev_origin_regex = (
    r"https?://("
    r"localhost|"
    r"127\.0\.0\.1|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?"
)

cors_kwargs = {
    "allow_origins": allowed_origins,
    "allow_credentials": True,
    "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    "allow_headers": ["Authorization", "Content-Type"],
}
if is_local_dev:
    cors_kwargs["allow_origin_regex"] = local_dev_origin_regex
    cors_kwargs["allow_methods"] = ["*"]
    cors_kwargs["allow_headers"] = ["*"]

# CORS middleware for frontend
app.add_middleware(CORSMiddleware, **cors_kwargs)

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
