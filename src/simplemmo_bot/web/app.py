"""FastAPI web application for bot control panel."""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import database as db
from .bot_manager import bot_manager, BotStatus

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="SimpleMMO Bot Panel",
    description="Web control panel for SimpleMMO Bot",
    version="0.1.0",
)

# Templates
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@app.on_event("startup")
async def startup() -> None:
    """Initialize on startup."""
    db.init_db()
    logger.info("Database initialized")


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
            "state": state,
            "total_stats": total_stats,
            "current_session": current_session,
        },
    )


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
