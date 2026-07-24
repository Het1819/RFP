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
from app.core.database import get_db
from app.core.host_validation import HostValidationMiddleware
from app.core.observability import (
    SAFE_ID_REGEX,
    MetricsRegistry,
    request_id_var,
    setup_logging,
)
from app.core.sessions.clock import SystemClock
from app.core.sessions.middleware import ServerSessionMiddleware
from app.core.sessions.store import RedisSessionStore, SessionStoreUnavailableError
from app.core.sessions.throttling import RedisThrottleStore
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

# Server-side session store (Phase A2). The default store/clock live on
# app.state so tests can substitute an in-memory store/fake clock per test
# without reconstructing the middleware; production always resolves to this
# Redis-backed default.
app.state.session_store = RedisSessionStore(settings.effective_session_redis_url)
app.state.session_clock = SystemClock()
app.state.throttle_store = RedisThrottleStore(settings.effective_session_redis_url)

app.add_middleware(
    ServerSessionMiddleware,
    default_store=app.state.session_store,
    cookie_name=settings.SESSION_COOKIE_NAME,
    idle_timeout_seconds=settings.SESSION_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds=settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
    https_only=settings.APP_ENV not in ("development", "local", "test"),
)

# Added last so it becomes the OUTERMOST middleware (Starlette runs the
# most-recently-added middleware first) -- an invalid Host is rejected
# before the session middleware ever touches Redis, and before any route
# or auth dependency runs. No-op when ALLOWED_HOSTS is unset (dev/test).
app.add_middleware(HostValidationMiddleware, allowed_hosts=settings.allowed_hosts_list)

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
async def readiness_check(
    request: Request, db: Session = Depends(get_db)
) -> dict[str, str]:
    import logging

    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
    except Exception as e:
        logging.getLogger(__name__).error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Database not ready") from e

    # Verify the session store: connectivity plus a minimal read/write/
    # delete round-trip, not just a ping. A required dependency being down
    # must make the app not-ready even if the database is healthy.
    store = getattr(request.app.state, "session_store", None)
    if store is not None:
        try:
            from app.core.sessions.models import SessionRecord

            probe_id = f"readyz-probe-{uuid.uuid4()}"
            probe_record = SessionRecord(created_at=0.0, last_activity_at=0.0)
            await store.save(probe_id, probe_record, ttl_seconds=5)
            await store.get(probe_id)
            await store.delete(probe_id)
        except SessionStoreUnavailableError as e:
            logging.getLogger(__name__).error(f"Readiness check failed: {e}")
            raise HTTPException(
                status_code=503, detail="Session store not ready"
            ) from e

    return {"status": "ready"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Any:
    return templates.TemplateResponse(request=request, name="index.html")
