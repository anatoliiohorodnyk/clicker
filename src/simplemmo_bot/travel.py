"""Travel module - handles step-by-step travel with NPC fights and material gathering."""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from .client import SimpleMMOClient, TravelResult
from .captcha import CaptchaSolver
from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class TravelStats:
    """Statistics for a travel session."""

    steps_taken: int = 0
    npcs_fought: int = 0
    npcs_won: int = 0
    npcs_lost: int = 0
    materials_gathered: int = 0
    items_found: int = 0
    gold_earned: int = 0
    exp_earned: int = 0
    captchas_solved: int = 0
    captchas_failed: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def duration(self) -> float:
        """Get session duration in seconds."""
        return time.time() - self.start_time

    @property
    def steps_per_minute(self) -> float:
        """Calculate steps per minute."""
        if self.duration == 0:
            return 0
        return (self.steps_taken / self.duration) * 60

    def __str__(self) -> str:
        """Format stats as string."""
        duration_mins = self.duration / 60
        return (
            f"=== Travel Stats ===\n"
            f"Duration: {duration_mins:.1f} minutes\n"
            f"Steps: {self.steps_taken} ({self.steps_per_minute:.1f}/min)\n"
            f"NPCs: {self.npcs_fought} fought, {self.npcs_won} won, {self.npcs_lost} lost\n"
            f"Materials: {self.materials_gathered}\n"
            f"Items: {self.items_found}\n"
            f"Gold: {self.gold_earned}\n"
            f"EXP: {self.exp_earned}\n"
            f"Captchas: {self.captchas_solved} solved, {self.captchas_failed} failed\n"
            f"Errors: {self.errors}"
        )


class TravelBot:
    """Bot for automated travel in SimpleMMO."""

    def __init__(
        self,
        settings: Settings,
        client: SimpleMMOClient,
        captcha_solver: CaptchaSolver,
    ) -> None:
        """Initialize travel bot."""
        self.settings = settings
        self.client = client
        self.captcha_solver = captcha_solver
        self.stats = TravelStats()
        self._running = False
        self._on_step_callback: Callable[[TravelResult, TravelStats], None] | None = None

    def on_step(self, callback: Callable[[TravelResult, TravelStats], None]) -> None:
        """Set callback for each step."""
        self._on_step_callback = callback

    def stop(self) -> None:
        """Stop the travel loop."""
        self._running = False
        logger.info("Stop requested")

    def _get_delay(self) -> float:
        """Get random delay between steps."""
        base = random.uniform(
            self.settings.step_delay_min,
            self.settings.step_delay_max,
        )
        # Add some jitter for more human-like behavior
        jitter = random.uniform(-0.5, 0.5)
        return max(1.0, base + jitter)

    def _handle_npc(self, result: TravelResult) -> None:
        """Handle NPC encounter - attack the NPC."""
        npc_id = result.data.get("npc_id")
        if not npc_id:
            logger.warning("NPC encounter but no npc_id in response")
            return

        npc_name = result.data.get("npc_name", "Unknown")
        logger.info(f"Attacking NPC: {npc_name} (ID: {npc_id})")

        self.stats.npcs_fought += 1

        attack_result = self.client.attack_npc(npc_id)

        if attack_result.get("success") or attack_result.get("win"):
            self.stats.npcs_won += 1
            exp = attack_result.get("exp", 0)
            gold = attack_result.get("gold", 0)
            self.stats.exp_earned += exp
            self.stats.gold_earned += gold
            logger.info(f"Won against {npc_name}! +{exp} XP, +{gold} gold")
        else:
            self.stats.npcs_lost += 1
            logger.info(f"Lost against {npc_name}")

    def _handle_material(self, result: TravelResult) -> None:
        """Handle material discovery - gather it."""
        material_id = result.data.get("material_id")
        if not material_id:
            logger.warning("Material found but no material_id in response")
            return

        material_name = result.data.get("material_name", "Unknown")
        logger.info(f"Gathering material: {material_name} (ID: {material_id})")

        gather_result = self.client.gather_material(material_id)

        if gather_result.get("success", True):
            self.stats.materials_gathered += 1
            logger.info(f"Gathered {material_name}")

    def _handle_captcha(self, result: TravelResult) -> bool:
        """
        Handle captcha verification.

        Returns:
            True if solved successfully, False otherwise.
        """
        logger.warning("Captcha required!")

        # Extract image URLs from response (structure may vary)
        raw = result.raw_response or {}
        image_urls = raw.get("images", raw.get("captcha_images", []))

        if not image_urls or len(image_urls) != 4:
            logger.error("Could not extract captcha images from response")
            self.stats.captchas_failed += 1
            return False

        # Solve captcha
        answer = self.captcha_solver.solve_from_urls(image_urls)

        if answer is None:
            logger.error("Failed to solve captcha")
            self.stats.captchas_failed += 1
            return False

        # TODO: Submit captcha answer via API
        # This would require knowing the exact endpoint and format
        logger.info(f"Captcha answer: {answer}")
        self.stats.captchas_solved += 1

        return True

    def travel(self, max_steps: int | None = None) -> TravelStats:
        """
        Start traveling.

        Args:
            max_steps: Maximum steps to take (None = use settings).

        Returns:
            Travel statistics.
        """
        max_steps = max_steps or self.settings.steps_per_session
        self._running = True
        self.stats = TravelStats()

        logger.info(f"Starting travel session (max {max_steps} steps)")

        while self._running and self.stats.steps_taken < max_steps:
            try:
                # Take a step
                result = self.client.travel_step()

                if result.captcha_required:
                    if not self._handle_captcha(result):
                        logger.error("Captcha failed, stopping")
                        break
                    continue

                if not result.success:
                    self.stats.errors += 1
                    logger.warning(f"Step failed: {result.message}")
                    time.sleep(30)
                    continue

                self.stats.steps_taken += 1

                # Handle different action types
                if result.action == "npc":
                    self._handle_npc(result)
                elif result.action == "material":
                    self._handle_material(result)
                elif result.action == "item":
                    self.stats.items_found += 1
                    logger.info(f"Found item: {result.data.get('item_name', 'Unknown')}")
                elif result.action == "gold":
                    gold = result.data.get("gold", 0)
                    self.stats.gold_earned += gold
                elif result.action == "exp":
                    exp = result.data.get("exp", 0)
                    self.stats.exp_earned += exp

                # Log progress
                if self.stats.steps_taken % 10 == 0:
                    logger.info(f"Progress: {self.stats.steps_taken}/{max_steps} steps")

                # Callback
                if self._on_step_callback:
                    self._on_step_callback(result, self.stats)

                # Wait before next step
                wait_time = max(result.wait_time, self._get_delay())
                logger.debug(f"Waiting {wait_time:.1f}s before next step")
                time.sleep(wait_time)

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                self.stats.errors += 1
                logger.error(f"Unexpected error: {e}")
                time.sleep(30)

        self._running = False
        logger.info(f"Travel session ended\n{self.stats}")

        return self.stats
