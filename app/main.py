"""FastAPI application."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import router
from app.auth import parse_auth_tokens
from app.config import get_settings
from app.db.session import database_ready, init_db
from app.errors import AppError
from app.logging_config import configure_logging, get_logger, request_id_var, task_var, user_var

log = get_logger("api")


def create_app() -> FastAPI:
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    configure_logging()
    app = FastAPI(title="Internal Document & CRM Assistant", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        task = task_var.set(request.url.path)
        user_token = user_var.set(_user_from_request(request))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse(
                status_code=500,
                content={"request_id": request_id, "error": {"code": "internal_error", "message": "Something went wrong."}},
            )
        duration_ms = int((time.perf_counter() - started) * 1000)
        log.info("request complete", extra={"duration_ms": duration_ms})
        response.headers["X-Request-Id"] = request_id
        user_var.reset(user_token)
        request_id_var.reset(token)
        task_var.reset(task)
        return response

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        request_id = getattr(_request.state, "request_id", None) or request_id_var.get()
        return JSONResponse(
            status_code=exc.status_code,
            content={"request_id": request_id, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        request_id = getattr(_request.state, "request_id", None) or request_id_var.get()
        return JSONResponse(
            status_code=400,
            content={
                "request_id": request_id,
                "error": {"code": "validation_failed", "message": "The request body is invalid."},
            },
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request):
        if database_ready():
            return {"request_id": getattr(request.state, "request_id", None), "status": "ready"}
        return JSONResponse(
            status_code=503,
            content={
                "request_id": getattr(request.state, "request_id", None),
                "error": {"code": "not_ready", "message": "A dependency is unavailable."},
            },
        )

    app.include_router(router, prefix="/v1")

    @app.on_event("startup")
    def startup() -> None:
        init_db()

    return app


def _user_from_request(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    user = parse_auth_tokens(get_settings().auth_tokens).get(token)
    return user.id if user else None


app = create_app()
