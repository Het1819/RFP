from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.csrf import generate_csrf_token

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["csrf_token"] = generate_csrf_token
