"""Main entry point for SimpleMMO Bot."""

import argparse
import logging
import sys
from pathlib import Path

from .config import get_settings, Settings
from .client import SimpleMMOClient
from .captcha import CaptchaSolver
from .travel import TravelBot, TravelStats
from .auth import auto_login


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)


def print_banner() -> None:
    """Print application banner."""
    banner = """
╔═══════════════════════════════════════════╗
║         SimpleMMO Travel Bot v0.1         ║
║     Automated Travel & Resource Farm      ║
╚═══════════════════════════════════════════╝
"""
    print(banner)


def on_step_update(result, stats: TravelStats) -> None:
    """Callback for each travel step."""
    # Simple console output for step results
    action_emoji = {
        "step": "🚶",
        "npc": "⚔️",
        "material": "⛏️",
        "item": "📦",
        "gold": "💰",
        "exp": "✨",
    }.get(result.action, "❓")

    print(f"  {action_emoji} Step {stats.steps_taken}: {result.message[:50]}")


def run_travel(settings: Settings, steps: int | None = None) -> TravelStats:
    """Run travel bot session."""
    with SimpleMMOClient(settings) as client:
        with CaptchaSolver(settings) as solver:
            bot = TravelBot(settings, client, solver)
            bot.on_step(on_step_update)

            return bot.travel(max_steps=steps)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="SimpleMMO Travel Bot - Automated travel and resource farming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    parser.add_argument(
        "-s", "--steps",
        type=int,
        default=None,
        help="Number of steps to take (overrides config)",
    )

    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env file (default: .env)",
    )

    args = parser.parse_args()

    # Setup
    setup_logging(args.verbose)
    print_banner()

    logger = logging.getLogger(__name__)

    # Load settings
    try:
        settings = get_settings()
        logger.info("Configuration loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        logger.error("Make sure .env file exists with required variables")
        logger.error("See .env.example for reference")
        return 1

    # Validate token
    if not settings.simplemmo_api_token or settings.simplemmo_api_token == "your_api_token_here":
        logger.error("SIMPLEMMO_API_TOKEN not configured")
        logger.error("Get your token from browser DevTools -> Network -> travel request")
        return 1

    if not settings.gemini_api_key or settings.gemini_api_key == "your_gemini_api_key_here":
        logger.error("GEMINI_API_KEY not configured")
        logger.error("Get your key from https://aistudio.google.com/app/apikey")
        return 1

    # Auto-login if session cookies not provided
    if not settings.simplemmo_laravel_session or not settings.simplemmo_xsrf_token:
        if settings.simplemmo_email and settings.simplemmo_password:
            logger.info("Session cookies not found, attempting auto-login...")
            credentials = auto_login(settings)

            if credentials:
                # Update settings with new cookies
                settings.simplemmo_laravel_session = credentials.laravel_session
                settings.simplemmo_xsrf_token = credentials.xsrf_token
                logger.info("Auto-login successful, session cookies obtained")
            else:
                logger.error("Auto-login failed")
                logger.error("Either fix login credentials or provide session cookies manually")
                return 1
        else:
            logger.warning("No session cookies and no login credentials provided")
            logger.warning("Some features (NPC fights, captcha) may not work")

    # Run bot
    try:
        logger.info("Starting travel bot...")
        stats = run_travel(settings, steps=args.steps)

        print("\n" + str(stats))

        if stats.errors > 10:
            logger.warning("High error count - check your configuration")
            return 1

        return 0

    except KeyboardInterrupt:
        logger.info("\nBot stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
