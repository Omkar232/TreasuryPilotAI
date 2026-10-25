from contextlib import asynccontextmanager
import re
import time
import uuid
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.api.routes import router
from app.core.config import settings
from app.db.database import Base, engine
from app import models  # noqa: F401
from app.core.logging import configure_logging

logger = configure_logging()

MAX_REQUEST_BODY_BYTES = 1_048_576


class RequestSizeLimitMiddleware:
    """Buffer and cap HTTP request bodies before passing them to FastAPI."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next((value for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"), None)
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                return
            if declared_length < 0 or declared_length > self.max_bytes:
                await JSONResponse({"detail": "Request body exceeds the 1 MiB limit"}, status_code=413)(scope, receive, send)
                return

        messages = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > self.max_bytes:
                    await JSONResponse({"detail": "Request body exceeds the 1 MiB limit"}, status_code=413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            else:
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)

@asynccontextmanager
async def lifespan(_app):
    if settings.app_env != "production":
        Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="TreasuryPilot AI", version="0.1.0", lifespan=lifespan,
              docs_url="/docs" if settings.app_env != "production" else None)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"])

@app.middleware("http")
async def request_logging(request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", supplied_id) else str(uuid.uuid4())
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.error("request_failed", extra={"request_id": request_id, "method": request.method,
            "path": request.url.path, "error_category": "unhandled"})
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info("http_request", extra={"request_id": request_id, "method": request.method,
        "path": request.url.path, "status_code": response.status_code,
        "duration_ms": round((time.monotonic() - start) * 1000, 2)})
    return response

@app.get("/api/health")
def health(): return {"status": "ok", "environment": settings.app_env}

@app.get("/api/health/ready")
def readiness():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database is unavailable")
    return {"status": "ready", "database": "available"}

app.include_router(router)
