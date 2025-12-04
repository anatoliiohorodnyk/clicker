"""Travel module - handles step-by-step travel with NPC fights and material gathering."""

import logging
import random
import re
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

    def _parse_npc_rewards(self, attack_result: dict) -> tuple[int, int]:
        """Parse gold and exp from NPC battle rewards HTML.

        Returns:
            Tuple of (gold, exp)
        """
        gold = 0
        exp = 0

        rewards = attack_result.get("rewards", [])
        if not rewards:
            return gold, exp

        for reward in rewards:
            reward_str = str(reward)
            # Parse EXP: "...>20,003 EXP"
            exp_match = re.search(r'>([\d,]+)\s*EXP', reward_str, re.IGNORECASE)
            if exp_match:
                exp = int(exp_match.group(1).replace(",", ""))

            # Parse Gold: "...>1,952  Gold"
            gold_match = re.search(r'>([\d,]+)\s*Gold', reward_str, re.IGNORECASE)
            if gold_match:
                gold = int(gold_match.group(1).replace(",", ""))

        return gold, exp

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

        # Log full result for debugging
        logger.debug(f"NPC attack result: {attack_result}")

        # Check for errors
        if attack_result.get("error"):
            logger.error(f"NPC attack failed: {attack_result.get('error')}")
            self.stats.npcs_lost += 1
            return

        # Determine win/loss
        won = (
            attack_result.get("type") == "success"
            or attack_result.get("win") is True
            or attack_result.get("opponent_hp", 1) <= 0
        )

        # Parse gold and exp from rewards HTML
        gold, exp = self._parse_npc_rewards(attack_result)

        # Debug: log rewards field if present
        if "rewards" in attack_result:
            logger.debug(f"NPC rewards field: {attack_result['rewards']}")
        else:
            logger.debug(f"No rewards field in attack result. Keys: {list(attack_result.keys())}")

        if won:
            self.stats.npcs_won += 1
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

        # Log full result for debugging
        logger.debug(f"Material gather result: {gather_result}")

        # Check for errors
        if gather_result.get("error"):
            logger.error(f"Material gather failed: {gather_result.get('error')}")
            return

        if gather_result.get("success", False):
            gather_count = gather_result.get("gather_count", 1)
            total_exp = gather_result.get("total_player_exp", 0)
            skill_exp = gather_result.get("total_skill_exp", 0)

            self.stats.materials_gathered += gather_count
            self.stats.exp_earned += total_exp

            logger.info(f"Gathered {gather_count}x {material_name}! +{total_exp} XP, +{skill_exp} skill XP")

    def _handle_captcha(self, result: TravelResult) -> bool:
        """
        Handle captcha verification.

        Returns:
            True if solved successfully, False otherwise.
        """
        logger.warning("Captcha detected! Attempting to solve...")

        # Use the new solve_captcha method that fetches images directly
        answer, prompt = self.captcha_solver.solve_captcha()

        # Check for "already verified" (answer == -1)
        if answer == -1:
            logger.info("Already verified - continuing without captcha submission")
            time.sleep(2)
            return True

        if answer is None:
            logger.error("Failed to solve captcha")
            self.stats.captchas_failed += 1
            return False

        logger.info(f"Captcha solved! Answer: {answer} for prompt: {prompt}")

        # Try to submit the answer
        success = self.captcha_solver.submit_captcha_answer(answer)

        if success:
            self.stats.captchas_solved += 1
            logger.info("Captcha submitted successfully, waiting before continuing...")
            time.sleep(3)  # Wait a bit after captcha
            return True
        else:
            # Even if submission fails, the captcha might have been solved
            # Try to continue anyway
            logger.warning("Captcha submission uncertain, attempting to continue...")
            self.stats.captchas_solved += 1
            time.sleep(3)
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

        # Set up break schedule
        next_break_at = random.randint(
            self.settings.break_interval_min,
            self.settings.break_interval_max
        )

        if max_steps == 0:
            logger.info("Starting travel session (infinite mode)")
        else:
            logger.info(f"Starting travel session (max {max_steps} steps)")
        logger.info(f"Next break scheduled at step {next_break_at}")

        while self._running and (max_steps == 0 or self.stats.steps_taken < max_steps):
            try:
                # Take a step
                result = self.client.travel_step()

                if result.captcha_required:
                    if not self._handle_captcha(result):
                        logger.warning("Captcha failed, waiting 60s before retry...")
                        time.sleep(60)
                    continue

                if not result.success:
                    self.stats.errors += 1
                    logger.warning(f"Step failed: {result.message}")
                    time.sleep(30)
                    continue

                self.stats.steps_taken += 1

                # Check if it's time for a break
                if self.stats.steps_taken >= next_break_at:
                    break_duration = random.randint(
                        self.settings.break_duration_min,
                        self.settings.break_duration_max
                    )
                    logger.info(f"☕ Taking a break for {break_duration // 60}m {break_duration % 60}s...")
                    time.sleep(break_duration)

                    # Schedule next break
                    next_break_at = self.stats.steps_taken + random.randint(
                        self.settings.break_interval_min,
                        self.settings.break_interval_max
                    )
                    logger.info(f"Break finished! Next break at step {next_break_at}")

                # Always accumulate gold and exp from every response
                step_gold = result.data.get("gold", 0)
                step_exp = result.data.get("exp", 0)
                if step_gold:
                    self.stats.gold_earned += step_gold
                if step_exp:
                    self.stats.exp_earned += step_exp

                # Handle different action types
                if result.action == "npc":
                    if self.settings.auto_fight_npc:
                        self._handle_npc(result)
                    else:
                        logger.info(f"NPC skipped (auto-fight disabled)")
                elif result.action == "material":
                    if self.settings.auto_gather_materials:
                        self._handle_material(result)
                    else:
                        logger.info(f"Material skipped (auto-gather disabled)")
                elif result.action == "item":
                    self.stats.items_found += 1
                    logger.info(f"Found item: {result.data.get('item_name', 'Unknown')}")

                # Log progress every 10 steps with accumulated stats
                if self.stats.steps_taken % 10 == 0:
                    logger.info(
                        f"Progress: {self.stats.steps_taken}/{max_steps} steps | "
                        f"Gold: {self.stats.gold_earned} | EXP: {self.stats.exp_earned}"
                    )

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
