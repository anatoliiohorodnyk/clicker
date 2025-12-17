"""Bot process manager for web panel - multi-account support."""

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

# Maximum concurrent bots
MAX_CONCURRENT_BOTS = 5


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
    account_id: int | None = None
    account_name: str | None = None
    session_id: int | None = None
    travel_stats: TravelStats | None = None
    quest_stats: QuestStats | None = None
    error_message: str | None = None
    started_at: float | None = None


class BotManager:
    """Manages multiple bot instances lifecycle and statistics."""

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
        # Multi-account support: dictionaries keyed by account_id
        self._states: dict[int, BotState] = {}
        self._bots: dict[int, TravelBot] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()
        self._on_stats_update: list[Callable[[int, TravelStats], None]] = []

    def get_state(self, account_id: int | None = None) -> BotState:
        """Get bot state for specific account or first running bot."""
        if account_id is not None:
            return self._states.get(account_id, BotState())
        # Legacy: return first running state or empty
        for state in self._states.values():
            if state.status in (BotStatus.RUNNING, BotStatus.STARTING):
                return state
        return BotState()

    def get_all_states(self) -> dict[int, BotState]:
        """Get all bot states."""
        return self._states.copy()

    def get_running_accounts(self) -> list[int]:
        """Get list of account IDs with running bots."""
        return [
            aid for aid, state in self._states.items()
            if state.status in (BotStatus.RUNNING, BotStatus.STARTING)
        ]

    def is_running(self, account_id: int | None = None) -> bool:
        """Check if bot is running for specific account or any account."""
        if account_id is not None:
            state = self._states.get(account_id)
            return state is not None and state.status in (BotStatus.RUNNING, BotStatus.STARTING)
        return len(self.get_running_accounts()) > 0

    def on_stats_update(self, callback: Callable[[int, TravelStats], None]) -> None:
        """Register callback for stats updates."""
        self._on_stats_update.append(callback)

    def _notify_stats_update(self, account_id: int, stats: TravelStats) -> None:
        """Notify all callbacks of stats update."""
        for callback in self._on_stats_update:
            try:
                callback(account_id, stats)
            except Exception as e:
                logger.error(f"Stats callback error: {e}")

    def _run_bot(self, account_id: int, account: db.Account, settings: Settings) -> None:
        """Run bot in thread for specific account."""
        state = self._states[account_id]
        try:
            state.status = BotStatus.RUNNING
            state.started_at = time.time()

            # Create database session
            state.session_id = db.create_session(account_id)
            db.add_log(state.session_id, "INFO", f"Bot session started for {account.name}")

            with SimpleMMOClient(settings) as client:
                # Update account level from game
                try:
                    player_info = client.get_player_info()
                    if player_info and "level" in player_info:
                        level_str = str(player_info["level"]).replace(",", "")
                        db.update_account_level(account_id, int(level_str))
                        logger.info(f"[{account.name}] Updated level: {level_str}")
                except Exception as e:
                    logger.warning(f"[{account.name}] Could not update level: {e}")

                # Auto-equip best items if enabled
                if account.auto_equip_best_items:
                    logger.info(f"[{account.name}] Auto-equip enabled - checking inventory...")
                    try:
                        result = client.equip_best_items()
                        if result.get("success"):
                            equipped_count = result.get("equipped", 0)
                            if equipped_count > 0:
                                db.add_log(
                                    state.session_id,
                                    "INFO",
                                    f"Auto-equipped {equipped_count} best items",
                                )
                            else:
                                logger.info(f"[{account.name}] No better items to equip")
                        else:
                            logger.warning(f"[{account.name}] Auto-equip failed: {result.get('error')}")
                    except Exception as e:
                        logger.warning(f"[{account.name}] Could not auto-equip: {e}")

                with CaptchaSolver(settings) as solver:
                    quest_bot = QuestBot(settings, client)
                    bot = TravelBot(settings, client, solver, quest_bot=quest_bot)
                    self._bots[account_id] = bot

                    # Set up stats callback
                    def on_step(result, stats: TravelStats, aid=account_id) -> None:
                        self._states[aid].travel_stats = stats
                        self._notify_stats_update(aid, stats)

                        # Update database periodically
                        if stats.steps_taken % 10 == 0:
                            db.update_session(
                                self._states[aid].session_id,
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

                    bot.on_step(on_step)

                    # Run travel
                    logger.info(f"[{account.name}] Starting travel...")
                    stats = bot.travel()
                    state.travel_stats = stats

                    # Final database update
                    if state.session_id:
                        db.update_session(
                            state.session_id,
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
                        db.end_session(state.session_id, "completed")
                        db.add_log(state.session_id, "INFO", f"Bot session completed for {account.name}")

        except Exception as e:
            logger.exception(f"[{account.name}] Bot error: {e}")
            state.status = BotStatus.ERROR
            state.error_message = str(e)
            if state.session_id:
                db.add_log(state.session_id, "ERROR", str(e))
                db.end_session(state.session_id, "error")
        finally:
            self._bots.pop(account_id, None)
            if state.status != BotStatus.ERROR:
                state.status = BotStatus.STOPPED

    def start(self, account_id: int | None = None) -> tuple[bool, str]:
        """Start the bot for specific account."""
        with self._lock:
            # If no account_id specified, use active account (legacy behavior)
            if account_id is None:
                active = db.get_active_account()
                if not active:
                    return False, "No account selected. Add and activate an account first."
                account_id = active.id

            # Check if already running
            if self.is_running(account_id):
                return False, "Bot is already running for this account"

            # Check max concurrent bots
            running_count = len(self.get_running_accounts())
            if running_count >= MAX_CONCURRENT_BOTS:
                return False, f"Maximum {MAX_CONCURRENT_BOTS} concurrent bots reached"

            # Get account
            account = db.get_account(account_id)
            if not account:
                return False, "Account not found"

            # Initialize state
            self._states[account_id] = BotState(
                status=BotStatus.STARTING,
                account_id=account_id,
                account_name=account.name,
            )

            try:
                settings = get_settings()

                # Apply captcha settings from database
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
                    if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
                        return False, "GEMINI_API_KEY not configured"
                    gemini_model = db.get_setting("gemini_model", "") or settings.gemini_model
                    settings.gemini_model = gemini_model

                # Apply timing settings from database
                step_delay_min = db.get_setting("step_delay_min", "")
                if step_delay_min:
                    settings.step_delay_min = int(step_delay_min)
                step_delay_max = db.get_setting("step_delay_max", "")
                if step_delay_max:
                    settings.step_delay_max = int(step_delay_max)
                break_interval_min = db.get_setting("break_interval_min", "")
                if break_interval_min:
                    settings.break_interval_min = int(break_interval_min)
                break_interval_max = db.get_setting("break_interval_max", "")
                if break_interval_max:
                    settings.break_interval_max = int(break_interval_max)
                break_duration_min = db.get_setting("break_duration_min", "")
                if break_duration_min:
                    settings.break_duration_min = int(break_duration_min)
                break_duration_max = db.get_setting("break_duration_max", "")
                if break_duration_max:
                    settings.break_duration_max = int(break_duration_max)

                # Apply feature settings from database
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
                quests_during_break = db.get_setting("quests_during_break", "")
                if quests_during_break:
                    settings.quests_during_break = quests_during_break.lower() == "true"

                # Set account credentials
                logger.info(f"Starting bot for account: {account.name} ({account.email})")
                settings.simplemmo_email = account.email
                settings.simplemmo_password = account.password
                settings.simplemmo_laravel_session = ""
                settings.simplemmo_xsrf_token = ""
                settings.simplemmo_api_token = ""

                # Auto-login
                logger.info(f"[{account.name}] Attempting auto-login...")
                credentials = auto_login(settings)
                if credentials:
                    settings.simplemmo_laravel_session = credentials.laravel_session
                    settings.simplemmo_xsrf_token = credentials.xsrf_token
                    if credentials.api_token:
                        settings.simplemmo_api_token = credentials.api_token
                else:
                    self._states.pop(account_id, None)
                    return False, f"Auto-login failed for {account.name}"

                if not settings.simplemmo_api_token:
                    self._states.pop(account_id, None)
                    return False, "API token not available"

                # Start bot in thread
                thread = threading.Thread(
                    target=self._run_bot,
                    args=(account_id, account, settings),
                    daemon=True,
                    name=f"bot-{account.name}",
                )
                self._threads[account_id] = thread
                thread.start()

                return True, f"Bot started for {account.name}"

            except Exception as e:
                self._states[account_id].status = BotStatus.ERROR
                self._states[account_id].error_message = str(e)
                return False, str(e)

    def stop(self, account_id: int | None = None) -> tuple[bool, str]:
        """Stop the bot for specific account or all bots."""
        with self._lock:
            if account_id is None:
                # Stop all bots
                running = self.get_running_accounts()
                if not running:
                    return False, "No bots are running"
                for aid in running:
                    self._stop_single(aid)
                return True, f"Stopped {len(running)} bot(s)"

            return self._stop_single(account_id)

    def _stop_single(self, account_id: int) -> tuple[bool, str]:
        """Stop a single bot."""
        state = self._states.get(account_id)
        if not state or state.status not in (BotStatus.RUNNING, BotStatus.STARTING):
            return False, "Bot is not running for this account"

        state.status = BotStatus.STOPPING
        account_name = state.account_name or f"Account {account_id}"

        # Stop the bot
        bot = self._bots.get(account_id)
        if bot:
            bot.stop()
            if state.session_id:
                db.add_log(state.session_id, "INFO", "Stop requested by user")

        # Wait for thread
        thread = self._threads.get(account_id)
        if thread and thread.is_alive():
            thread.join(timeout=10)

        # Final stats update
        if state.session_id and state.travel_stats:
            stats = state.travel_stats
            db.update_session(
                state.session_id,
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
            db.end_session(state.session_id, "stopped")

        state.status = BotStatus.STOPPED
        self._threads.pop(account_id, None)

        return True, f"Bot stopped for {account_name}"


# Global instance
bot_manager = BotManager()
