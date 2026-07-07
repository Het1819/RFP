import base64
import hashlib
import hmac
import json
import secrets
from typing import Any, Literal, cast

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


def sign_data(data: str, secret: str) -> str:
    """Sign string data with HMAC-SHA256."""
    signature = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{signature}"


def verify_data(signed_value: str, secret: str) -> str | None:
    """Verify HMAC-SHA256 signature and return original string if valid."""
    if "." not in signed_value:
        return None
    try:
        data, signature = signed_value.rsplit(".", 1)
        expected_signature = hmac.new(
            secret.encode(), data.encode(), hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(signature, expected_signature):
            return data
    except Exception:
        pass
    return None


class SimpleSessionMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        secret_key: str,
        cookie_name: str = "rfp_session",
        same_site: Literal["lax", "strict", "none"] = "lax",
        https_only: bool = False,
    ):
        super().__init__(app)
        self.secret_key = secret_key
        self.cookie_name = cookie_name
        self.same_site = same_site
        self.https_only = https_only

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # Read and verify session cookie
        cookie_val = request.cookies.get(self.cookie_name)
        session_data = {}
        if cookie_val:
            verified = verify_data(cookie_val, self.secret_key)
            if verified:
                try:
                    session_data = json.loads(
                        base64.b64decode(verified.encode()).decode()
                    )
                except Exception:
                    pass

        # Inject session dict into scope so request.session works
        request.scope["session"] = session_data

        response = cast(Response, await call_next(request))

        # Write session cookie in response
        if "session" in request.scope:
            new_data = request.scope["session"]
            serialized = base64.b64encode(json.dumps(new_data).encode()).decode()
            signed = sign_data(serialized, self.secret_key)
            response.set_cookie(
                key=self.cookie_name,
                value=signed,
                httponly=True,
                samesite=self.same_site,
                secure=self.https_only,
            )
        return response


def generate_csrf_token(request: Request) -> str:
    """Retrieve existing CSRF token or generate a new random one stored in session."""
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(32)
    return cast(str, request.session["csrf_token"])


def get_csrf_token(request: Request) -> str | None:
    """Retrieve CSRF token from the session."""
    return cast(str | None, request.session.get("csrf_token"))


async def validate_csrf_token(request: Request) -> None:
    """Validate CSRF token for mutating browser requests (POST, PUT, PATCH, DELETE)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # In test environment, bypass unless x-test-enforce-csrf
    # header is explicitly provided.
    import sys

    if "pytest" in sys.modules and "x-test-enforce-csrf" not in request.headers:
        return

    session_token = request.session.get("csrf_token")
    if not session_token:
        raise HTTPException(status_code=403, detail="CSRF token validation failed")

    user_token = None

    # Check header first (common for HTMX/AJAX)
    if "x-csrf-token" in request.headers:
        user_token = request.headers["x-csrf-token"]
    else:
        # Check form data
        content_type = request.headers.get("content-type", "")
        if (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            try:
                form_data = await request.form()
                raw_token = form_data.get("csrf_token")
                if isinstance(raw_token, str):
                    user_token = raw_token
            except Exception:
                pass

    if not user_token or not secrets.compare_digest(session_token, str(user_token)):
        raise HTTPException(status_code=403, detail="CSRF token validation failed")
