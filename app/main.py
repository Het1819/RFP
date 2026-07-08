import time
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.csrf import SimpleSessionMiddleware
from app.core.database import get_db
from app.core.observability import (
    SAFE_ID_REGEX,
    MetricsRegistry,
    request_id_var,
    setup_logging,
)
from app.core.templates import templates
from app.web.routes.auth import router as auth_router
from app.web.routes.compliance import router as compliance_router
from app.web.routes.feedback import router as feedback_router
from app.web.routes.projects import router as projects_router

# Setup JSON logging baseline immediately
setup_logging()

app = FastAPI(
    title="RFP Architect MVP",
    description="Human-in-the-loop proposal generation system",
    version="0.1.0",
)


class CorrelationIdAndMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/static"):
            return cast(Response, await call_next(request))

        incoming_id = request.headers.get("x-request-id")
        if incoming_id and SAFE_ID_REGEX.match(incoming_id):
            request_id = incoming_id
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        token = request_id_var.set(request_id)

        start_time = time.time()
        try:
            response = cast(Response, await call_next(request))
            latency_ms = int((time.time() - start_time) * 1000)

            # Record in-memory HTTP metrics
            route = request.url.path
            method = request.method
            status_code = response.status_code

            key = (route, method, status_code)
            MetricsRegistry.request_counts[key] = (
                MetricsRegistry.request_counts.get(key, 0) + 1
            )

            latency_key = (route, method)
            latencies = MetricsRegistry.request_latencies.setdefault(latency_key, [])
            latencies.append(latency_ms)
            if len(latencies) > 100:
                latencies.pop(0)

            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_var.reset(token)


app.add_middleware(CorrelationIdAndMetricsMiddleware)

# Add session middleware for CSRF token storage
app.add_middleware(
    SimpleSessionMiddleware,
    secret_key=cast(str, settings.SESSION_SECRET_KEY),
    cookie_name="rfp_session",
    same_site="lax",
    https_only=settings.APP_ENV not in ("development", "local", "test"),
)

BASE_DIR = Path(__file__).resolve().parent

# Mount static files
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Templates

# Include routers
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(compliance_router)
app.include_router(feedback_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Any:
    if exc.status_code == 401:
        accept = request.headers.get("accept", "")
        if request.method == "GET" and "text/html" in accept:
            return RedirectResponse(url="/login", status_code=303)
    from fastapi.exception_handlers import (
        http_exception_handler as default_http_exception_handler,
    )

    return await default_http_exception_handler(request, exc)


@app.get("/metrics")
def metrics_endpoint(db: Session = Depends(get_db)) -> Response:
    from app.core.observability import generate_prometheus_metrics

    content = generate_prometheus_metrics(db)
    return Response(content=content, media_type="text/plain; version=0.0.4")


@app.get("/health")
@app.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readiness_check(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Database not ready") from e


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Any:
    return templates.TemplateResponse(request=request, name="index.html")
