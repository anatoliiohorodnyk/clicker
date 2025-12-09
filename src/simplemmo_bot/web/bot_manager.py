"""Bot process manager for web panel."""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from ..config import get_settings, Settings
from ..client import SimpleMMOClient
from ..captcha import CaptchaSolver
from ..travel import TravelBot, TravelStats
from ..quests import QuestBot, QuestStats
from ..auth import auto_login
from . import database as db

logger = logging.getLogger(__name__)


class BotStatus(str, Enum):
    """Bot running status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class BotState:
    """Current bot state."""

    status: BotStatus = BotStatus.STOPPED
    session_id: int | None = None
    travel_stats: TravelStats | None = None
    quest_stats: QuestStats | None = None
    error_message: str | None = None
    started_at: float | None = None


class BotManager:
    """Manages bot lifecycle and statistics."""

    _instance: "BotManager | None" = None

    def __new__(cls) -> "BotManager":
        """Singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize bot manager."""
        if self._initialized:
            return

        self._initialized = True
        self.state = BotState()
        self._bot: TravelBot | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._on_stats_update: list[Callable[[TravelStats], None]] = []

    def get_state(self) -> BotState:
        """Get current bot state."""
        return self.state

    def is_running(self) -> bool:
        """Check if bot is running."""
        return self.state.status in (BotStatus.RUNNING, BotStatus.STARTING)

    def on_stats_update(self, callback: Callable[[TravelStats], None]) -> None:
        """Register callback for stats updates."""
        self._on_stats_update.append(callback)

    def _notify_stats_update(self, stats: TravelStats) -> None:
        """Notify all callbacks of stats update."""
        for callback in self._on_stats_update:
            try:
                callback(stats)
            except Exception as e:
                logger.error(f"Stats callback error: {e}")

    def _run_bot(self, settings: Settings) -> None:
        """Run bot in thread."""
        try:
            self.state.status = BotStatus.RUNNING
            self.state.started_at = time.time()

            # Create database session
            self.state.session_id = db.create_session()
            db.add_log(self.state.session_id, "INFO", "Bot session started")

            with SimpleMMOClient(settings) as client:
                # Update account level from game
                active_account = db.get_active_account()
                if active_account:
                    try:
                        player_info = client.get_player_info()
                        if player_info and "level" in player_info:
                            # Remove commas from level string (e.g. "3,503" -> "3503")
                            level_str = str(player_info["level"]).replace(",", "")
                            db.update_account_level(active_account.id, int(level_str))
                            logger.info(f"Updated account level: {level_str}")
                    except Exception as e:
                        logger.warning(f"Could not update account level: {e}")

                    # Auto-equip best items if enabled for this account
                    if active_account.auto_equip_best_items:
                        logger.info("Auto-equip best items enabled - checking inventory...")
                        try:
                            result = client.equip_best_items()
                            if result.get("success"):
                                equipped_count = result.get("equipped", 0)
                                if equipped_count > 0:
                                    db.add_log(
                                        self.state.session_id,
                                        "INFO",
                                        f"Auto-equipped {equipped_count} best items",
                                    )
                                else:
                                    logger.info("No better items to equip")
                            else:
                                logger.warning(f"Auto-equip failed: {result.get('error')}")
                        except Exception as e:
                            logger.warning(f"Could not auto-equip items: {e}")

                with CaptchaSolver(settings) as solver:
                    quest_bot = QuestBot(settings, client)
                    self._bot = TravelBot(settings, client, solver, quest_bot=quest_bot)

                    # Set up stats callback
                    def on_step(result, stats: TravelStats) -> None:
                        self.state.travel_stats = stats
                        self._notify_stats_update(stats)

                        # Update database periodically (every 10 steps)
                        if stats.steps_taken % 10 == 0:
                            db.update_session(
                                self.state.session_id,
                                steps_taken=stats.steps_taken,
                                npcs_fought=stats.npcs_fought,
                                npcs_won=stats.npcs_won,
                                materials_gathered=stats.materials_gathered,
                                items_found=stats.items_found,
                                gold_earned=stats.gold_earned,
                                exp_earned=stats.exp_earned,
                                captchas_solved=stats.captchas_solved,
                                errors=stats.errors,
                            )

                    self._bot.on_step(on_step)

                    # Run travel
                    stats = self._bot.travel()
                    self.state.travel_stats = stats

                    # Final database update
                    if self.state.session_id:
                        db.update_session(
                            self.state.session_id,
                            steps_taken=stats.steps_taken,
                            npcs_fought=stats.npcs_fought,
                            npcs_won=stats.npcs_won,
                            materials_gathered=stats.materials_gathered,
                            items_found=stats.items_found,
                            gold_earned=stats.gold_earned,
                            exp_earned=stats.exp_earned,
                            captchas_solved=stats.captchas_solved,
                            errors=stats.errors,
                        )
                        db.end_session(self.state.session_id, "completed")
                        db.add_log(self.state.session_id, "INFO", "Bot session completed")

        except Exception as e:
            logger.exception(f"Bot error: {e}")
            self.state.status = BotStatus.ERROR
            self.state.error_message = str(e)
            if self.state.session_id:
                db.add_log(self.state.session_id, "ERROR", str(e))
                db.end_session(self.state.session_id, "error")
        finally:
            self._bot = None
            if self.state.status != BotStatus.ERROR:
                self.state.status = BotStatus.STOPPED

    def start(self) -> tuple[bool, str]:
        """Start the bot."""
        with self._lock:
            if self.is_running():
                return False, "Bot is already running"

            self.state = BotState(status=BotStatus.STARTING)

            try:
                settings = get_settings()

                # Apply captcha settings from database (with .env as fallback)
                captcha_provider = db.get_setting("captcha_provider", "") or settings.captcha_provider
                settings.captcha_provider = captcha_provider

                if captcha_provider == "openai":
                    openai_api_key = db.get_setting("openai_api_key", "") or settings.openai_api_key
                    if not openai_api_key:
                        return False, "OPENAI_API_KEY not configured for OpenAI provider"
                    settings.openai_api_key = openai_api_key
                    settings.openai_api_base = db.get_setting("openai_api_base", "") or settings.openai_api_base
                    settings.openai_model = db.get_setting("openai_model", "") or settings.openai_model
                else:
                    # Gemini provider
                    if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
                        return False, "GEMINI_API_KEY not configured"
                    gemini_model = db.get_setting("gemini_model", "") or settings.gemini_model
                    settings.gemini_model = gemini_model

                # Apply feature settings from database (with .env as fallback)
                auto_fight = db.get_setting("auto_fight_npc", "")
                if auto_fight:
                    settings.auto_fight_npc = auto_fight.lower() == "true"
                auto_gather = db.get_setting("auto_gather_materials", "")
                if auto_gather:
                    settings.auto_gather_materials = auto_gather.lower() == "true"
                use_healer = db.get_setting("use_healer", "")
                if use_healer:
                    settings.use_healer = use_healer.lower() == "true"
                only_quests = db.get_setting("only_quests", "")
                if only_quests:
                    settings.only_quests = only_quests.lower() == "true"

                # Get active account from database
                active_account = db.get_active_account()
                if active_account:
                    logger.info(f"Using account: {active_account.name} ({active_account.email})")
                    settings.simplemmo_email = active_account.email
                    settings.simplemmo_password = active_account.password
                    # Clear cached tokens to force re-login with new account
                    settings.simplemmo_laravel_session = ""
                    settings.simplemmo_xsrf_token = ""
                    settings.simplemmo_api_token = ""

                # Auto-login if needed
                needs_login = (
                    not settings.simplemmo_laravel_session
                    or not settings.simplemmo_xsrf_token
                    or not settings.simplemmo_api_token
                    or settings.simplemmo_api_token == "your_api_token_here"
                )

                if needs_login:
                    if settings.simplemmo_email and settings.simplemmo_password:
                        logger.info("Attempting auto-login...")
                        credentials = auto_login(settings)
                        if credentials:
                            settings.simplemmo_laravel_session = credentials.laravel_session
                            settings.simplemmo_xsrf_token = credentials.xsrf_token
                            if credentials.api_token:
                                settings.simplemmo_api_token = credentials.api_token
                        else:
                            return False, "Auto-login failed"
                    else:
                        return False, "No account selected. Add and activate an account first."

                if not settings.simplemmo_api_token or settings.simplemmo_api_token == "your_api_token_here":
                    return False, "API token not available"

                # Start bot in thread
                self._thread = threading.Thread(
                    target=self._run_bot,
                    args=(settings,),
                    daemon=True,
                )
                self._thread.start()

                return True, "Bot started"

            except Exception as e:
                self.state.status = BotStatus.ERROR
                self.state.error_message = str(e)
                return False, str(e)

    def stop(self) -> tuple[bool, str]:
        """Stop the bot."""
        with self._lock:
            if not self.is_running():
                return False, "Bot is not running"

            self.state.status = BotStatus.STOPPING

            if self._bot:
                self._bot.stop()
                if self.state.session_id:
                    db.add_log(self.state.session_id, "INFO", "Stop requested by user")

            # Wait for thread to finish (with timeout)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=10)

            if self.state.session_id:
                # Final stats update
                if self.state.travel_stats:
                    stats = self.state.travel_stats
                    db.update_session(
                        self.state.session_id,
                        steps_taken=stats.steps_taken,
                        npcs_fought=stats.npcs_fought,
                        npcs_won=stats.npcs_won,
                        materials_gathered=stats.materials_gathered,
                        items_found=stats.items_found,
                        gold_earned=stats.gold_earned,
                        exp_earned=stats.exp_earned,
                        captchas_solved=stats.captchas_solved,
                        errors=stats.errors,
                    )
                db.end_session(self.state.session_id, "stopped")

            self.state.status = BotStatus.STOPPED
            return True, "Bot stopped"


# Global instance
bot_manager = BotManager()
