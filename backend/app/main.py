
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# --------------------------------------------------
# Environment configuration
# --------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

# A backend-specific file wins, while the project-level file remains a
# convenient fallback for local runs. Real environment variables take priority.
load_dotenv(ENV_FILE, override=False)
load_dotenv(BACKEND_DIR.parent / ".env", override=False)

# --------------------------------------------------
# Logging configuration
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# Hide HTTP request URLs that may contain API keys.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --------------------------------------------------
# Database and router imports
# --------------------------------------------------

from app.services.database import init_db
from app.services.db_connection import (
    RequestDatabaseSessionMiddleware,
    connection_target,
)
from app.services.performance import PerformanceLoggingMiddleware
from app.routers import (
    auth,
    chat,
    documents,
    conversations,
    tools,
    export,
)

# --------------------------------------------------
# Application lifespan
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Document Chatbot API...")
    from app.services.llm_service import (
        close_shared_http_client,
        open_shared_http_client,
    )

    open_shared_http_client()
    try:
        try:
            provider, target, source = connection_target()
            if provider == "Turso":
                logger.info(
                    "Active database provider: %s | host=%s",
                    provider,
                    target,
                )
            else:
                logger.info(
                    "Active database provider: %s | path=%s | path_source=%s",
                    provider,
                    target,
                    source,
                )
            init_db()
            logger.info("Database initialized successfully.")
        except Exception:
            logger.exception("Database initialization failed.")
            raise
        yield
    finally:
        await close_shared_http_client()
        logger.info("AI Document Chatbot API stopped.")

# --------------------------------------------------
# FastAPI application
# --------------------------------------------------

app = FastAPI(
    title="AI Document Chatbot API",
    description=(
        "AI-powered document assistant with multi-document RAG, "
        "conversation history, document analysis and exports."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# --------------------------------------------------
# CORS configuration
# --------------------------------------------------

DEFAULT_FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://10.188.37.22:3000",
    "http://172.22.208.1:3000",
]

configured_origins = os.getenv("FRONTEND_URL", "")

frontend_origins = set(DEFAULT_FRONTEND_ORIGINS)

frontend_origins.update(
    origin.strip().rstrip("/")
    for origin in configured_origins.split(",")
    if origin.strip()
)

allowed_origins = sorted(frontend_origins)

logger.info("Allowed frontend origins: %s", allowed_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)
app.add_middleware(RequestDatabaseSessionMiddleware)
app.add_middleware(PerformanceLoggingMiddleware)

# --------------------------------------------------
# Register API routers
# --------------------------------------------------

app.include_router(
    chat.router,
    prefix="/api/chat",
    tags=["Chat"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])

app.include_router(
    documents.router,
    prefix="/api/documents",
    tags=["Documents"],
)

app.include_router(
    conversations.router,
    prefix="/api/conversations",
    tags=["Conversations"],
)

app.include_router(
    tools.router,
    prefix="/api/tools",
    tags=["AI Tools"],
)

app.include_router(
    export.router,
    prefix="/api/export",
    tags=["Export"],
)

# --------------------------------------------------
# Health check
# --------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    return {
        "success": True,
        "message": "AI Document Chatbot API is running.",
        "version": app.version,
    }


@app.get("/api/health", tags=["Health"])
async def health_check():
    return {
        "success": True,
        "status": "healthy",
        "service": "AI Document Chatbot API",
    }

# --------------------------------------------------
# HTTP exception handler
# --------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.exception(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "An unexpected server error occurred.",
        },
    )
