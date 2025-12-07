"""FastAPI web application for bot control panel."""

import hashlib
import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from . import database as db
from .bot_manager import bot_manager, BotStatus
from ..config import get_settings, Settings

logger = logging.getLogger(__name__)

# Auth credentials (hashed)
AUTH_USERNAME = "just_lord"
AUTH_PASSWORD_HASH = hashlib.sha256("zZ2486173950@".encode()).hexdigest()

# Static files and templates
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware."""

    async def dispatch(self, request: Request, call_next):
        public_paths = ["/login", "/favicon.ico", "/static"]

        if not any(request.url.path.startswith(p) for p in public_paths):
            user = request.session.get("user")
            if not user:
                if request.url.path.startswith(("/api", "/partials", "/actions")):
                    return HTMLResponse(status_code=401, content="Unauthorized")
                return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)


# Initialize FastAPI app
app = FastAPI(
    title="SimpleMMO Bot Panel",
    description="Web control panel for SimpleMMO Bot",
    version="0.1.0",
)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Add middleware (order matters: last added = first executed)
# AuthMiddleware needs SessionMiddleware, so SessionMiddleware must be added AFTER (executed first)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=secrets.token_hex(32),
    session_cookie="bot_session",
    max_age=86400 * 7,  # 7 days
)

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_current_user(request: Request) -> str | None:
    """Get current authenticated user from session."""
    return request.session.get("user")


@app.on_event("startup")
async def startup() -> None:
    """Initialize on startup."""
    db.init_db()
    logger.info("Database initialized")


# Favicon
@app.get("/favicon.ico")
async def favicon():
    """Serve favicon."""
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


# Auth routes
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Login page."""
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)) -> HTMLResponse:
    """Process login."""
    password_hash = hashlib.sha256(password.encode()).hexdigest()

    if username == AUTH_USERNAME and password_hash == AUTH_PASSWORD_HASH:
        request.session["user"] = username
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password"},
    )


@app.get("/logout")
async def logout(request: Request):
    """Logout user."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# Dashboard
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard page."""
    state = bot_manager.get_state()
    total_stats = db.get_total_stats()
    current_session = db.get_current_session()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "page": "dashboard",
            "state": state,
            "total_stats": total_stats,
            "current_session": current_session,
        },
    )


# Settings page
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Settings page."""
    settings = get_settings()
    # Get captcha settings from database or use defaults
    captcha_provider = db.get_setting("captcha_provider", settings.captcha_provider)
    gemini_model = db.get_setting("gemini_model", settings.gemini_model)
    openai_api_base = db.get_setting("openai_api_base", settings.openai_api_base)
    openai_api_key = db.get_setting("openai_api_key", settings.openai_api_key)
    openai_model = db.get_setting("openai_model", settings.openai_model)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "page": "settings",
            "settings": settings,
            "captcha_provider": captcha_provider,
            "gemini_model": gemini_model,
            "openai_api_base": openai_api_base,
            "openai_api_key": openai_api_key,
            "openai_model": openai_model,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    step_delay_min: int = Form(...),
    step_delay_max: int = Form(...),
    break_interval_min: int = Form(...),
    break_interval_max: int = Form(...),
    break_duration_min: int = Form(...),
    break_duration_max: int = Form(...),
    auto_fight_npc: bool = Form(False),
    auto_gather_materials: bool = Form(False),
    only_quests: bool = Form(False),
    captcha_provider: str = Form("gemini"),
    gemini_model: str = Form("gemini-2.0-flash"),
    openai_api_base: str = Form("https://api.openai.com/v1"),
    openai_api_key: str = Form(""),
    openai_model: str = Form("gpt-4o"),
) -> HTMLResponse:
    """Save settings to database."""
    # Save to database
    db.set_setting("step_delay_min", str(step_delay_min))
    db.set_setting("step_delay_max", str(step_delay_max))
    db.set_setting("break_interval_min", str(break_interval_min))
    db.set_setting("break_interval_max", str(break_interval_max))
    db.set_setting("break_duration_min", str(break_duration_min))
    db.set_setting("break_duration_max", str(break_duration_max))
    db.set_setting("auto_fight_npc", "true" if auto_fight_npc else "false")
    db.set_setting("auto_gather_materials", "true" if auto_gather_materials else "false")
    db.set_setting("only_quests", "true" if only_quests else "false")
    db.set_setting("captcha_provider", captcha_provider)
    db.set_setting("gemini_model", gemini_model)
    db.set_setting("openai_api_base", openai_api_base)
    db.set_setting("openai_api_key", openai_api_key)
    db.set_setting("openai_model", openai_model)

    return RedirectResponse(url="/settings?saved=1", status_code=302)


# Accounts page
@app.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request) -> HTMLResponse:
    """Accounts management page."""
    accounts = db.get_accounts()
    message = request.query_params.get("message")
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "accounts.html",
        {
            "request": request,
            "page": "accounts",
            "accounts": accounts,
            "message": message,
            "error": error,
        },
    )


@app.post("/accounts", response_class=HTMLResponse)
async def manage_accounts(
    request: Request,
    action: str = Form(...),
    account_id: int = Form(None),
    name: str = Form(None),
    email: str = Form(None),
    password: str = Form(None),
) -> HTMLResponse:
    """Handle account management actions."""
    try:
        if action == "create":
            if not name or not email or not password:
                return RedirectResponse(url="/accounts?error=All+fields+required", status_code=302)
            db.create_account(name, email, password)
            return RedirectResponse(url="/accounts?message=Account+created", status_code=302)

        elif action == "update":
            if not account_id or not name or not email:
                return RedirectResponse(url="/accounts?error=Invalid+data", status_code=302)
            account = db.get_account(account_id)
            if not account:
                return RedirectResponse(url="/accounts?error=Account+not+found", status_code=302)
            # Use existing password if not provided
            new_password = password if password else account.password
            db.update_account(account_id, name, email, new_password)
            return RedirectResponse(url="/accounts?message=Account+updated", status_code=302)

        elif action == "delete":
            if not account_id:
                return RedirectResponse(url="/accounts?error=Invalid+account", status_code=302)
            db.delete_account(account_id)
            return RedirectResponse(url="/accounts?message=Account+deleted", status_code=302)

        elif action == "activate":
            if not account_id:
                return RedirectResponse(url="/accounts?error=Invalid+account", status_code=302)
            db.set_active_account(account_id)
            return RedirectResponse(url="/accounts?message=Account+activated", status_code=302)

    except Exception as e:
        logger.error(f"Account action error: {e}")
        return RedirectResponse(url=f"/accounts?error={str(e)}", status_code=302)

    return RedirectResponse(url="/accounts", status_code=302)


# API routes
@app.get("/api/status")
async def get_status() -> dict:
    """Get current bot status."""
    state = bot_manager.get_state()
    return {
        "status": state.status.value,
        "session_id": state.session_id,
        "error": state.error_message,
        "stats": {
            "steps_taken": state.travel_stats.steps_taken if state.travel_stats else 0,
            "npcs_fought": state.travel_stats.npcs_fought if state.travel_stats else 0,
            "npcs_won": state.travel_stats.npcs_won if state.travel_stats else 0,
            "materials_gathered": state.travel_stats.materials_gathered if state.travel_stats else 0,
            "gold_earned": state.travel_stats.gold_earned if state.travel_stats else 0,
            "exp_earned": state.travel_stats.exp_earned if state.travel_stats else 0,
        } if state.travel_stats else None,
    }


@app.post("/api/start")
async def start_bot() -> dict:
    """Start the bot."""
    success, message = bot_manager.start()
    return {"success": success, "message": message}


@app.post("/api/stop")
async def stop_bot() -> dict:
    """Stop the bot."""
    success, message = bot_manager.stop()
    return {"success": success, "message": message}


@app.get("/api/stats")
async def get_stats() -> dict:
    """Get all statistics."""
    return {
        "total": db.get_total_stats(),
        "current": bot_manager.get_state().travel_stats,
    }


@app.get("/api/logs")
async def get_logs(limit: int = 50) -> list[dict]:
    """Get recent logs."""
    return db.get_recent_logs(limit)


# HTMX partials
@app.get("/partials/status", response_class=HTMLResponse)
async def status_partial(request: Request) -> HTMLResponse:
    """Status badge partial for HTMX polling."""
    state = bot_manager.get_state()
    return templates.TemplateResponse(
        "partials/status.html",
        {"request": request, "state": state},
    )


@app.get("/partials/stats", response_class=HTMLResponse)
async def stats_partial(request: Request) -> HTMLResponse:
    """Stats cards partial for HTMX polling."""
    state = bot_manager.get_state()
    total_stats = db.get_total_stats()
    return templates.TemplateResponse(
        "partials/stats.html",
        {"request": request, "state": state, "total_stats": total_stats},
    )


@app.get("/partials/controls", response_class=HTMLResponse)
async def controls_partial(request: Request) -> HTMLResponse:
    """Control buttons partial for HTMX."""
    state = bot_manager.get_state()
    return templates.TemplateResponse(
        "partials/controls.html",
        {"request": request, "state": state},
    )


# HTMX actions
@app.post("/actions/start", response_class=HTMLResponse)
async def action_start(request: Request) -> HTMLResponse:
    """Start bot action for HTMX."""
    bot_manager.start()
    state = bot_manager.get_state()
    return templates.TemplateResponse(
        "partials/controls.html",
        {"request": request, "state": state},
    )


@app.post("/actions/stop", response_class=HTMLResponse)
async def action_stop(request: Request) -> HTMLResponse:
    """Stop bot action for HTMX."""
    bot_manager.stop()
    state = bot_manager.get_state()
    return templates.TemplateResponse(
        "partials/controls.html",
        {"request": request, "state": state},
    )
