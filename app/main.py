from pathlib import Path
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.csrf import SimpleSessionMiddleware
from app.core.database import get_db
from app.core.templates import templates
from app.web.routes.auth import router as auth_router
from app.web.routes.compliance import router as compliance_router

# Ensure configuration is loaded and database models/connections are importable
from app.web.routes.projects import router as projects_router

app = FastAPI(
    title="RFP Architect MVP",
    description="Human-in-the-loop proposal generation system",
    version="0.1.0",
)

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
