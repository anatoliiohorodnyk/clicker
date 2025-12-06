"""Quests module - handles automated quest completion."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .client import SimpleMMOClient, human_delay
from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class QuestStats:
    """Statistics for a quest session."""

    quests_attempted: int = 0
    quests_succeeded: int = 0
    quests_failed: int = 0
    gold_earned: int = 0
    exp_earned: int = 0
    quest_points_used: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.time)

    @property
    def duration(self) -> float:
        """Get session duration in seconds."""
        return time.time() - self.start_time

    def __str__(self) -> str:
        """Format stats as string."""
        duration_mins = self.duration / 60
        success_rate = (
            (self.quests_succeeded / self.quests_attempted * 100)
            if self.quests_attempted > 0
            else 0
        )
        return (
            f"=== Quest Stats ===\n"
            f"Duration: {duration_mins:.1f} minutes\n"
            f"Quests: {self.quests_attempted} attempted, {self.quests_succeeded} succeeded ({success_rate:.0f}%)\n"
            f"Quest Points Used: {self.quest_points_used}\n"
            f"Gold: {self.gold_earned}\n"
            f"EXP: {self.exp_earned}\n"
            f"Errors: {self.errors}"
        )


class QuestBot:
    """Bot for automated quest completion in SimpleMMO."""

    def __init__(
        self,
        settings: Settings,
        client: SimpleMMOClient,
    ) -> None:
        """Initialize quest bot."""
        self.settings = settings
        self.client = client
        self.stats = QuestStats()
        self._running = False

    def stop(self) -> None:
        """Stop the quest loop."""
        self._running = False
        logger.info("Stop requested")

    def _select_best_quest(self, quests: list[dict]) -> dict | None:
        """
        Select the best quest to perform.

        Strategy: Pick quest with lowest level_required that has success_chance > 0.

        Args:
            quests: List of available quests.

        Returns:
            Best quest dict or None if no suitable quest found.
        """
        # Filter quests with success_chance > 0 and not completed
        available = [
            q for q in quests
            if q.get("success_chance", 0) > 0 and not q.get("is_completed", False)
        ]

        if not available:
            logger.warning("No quests with success_chance > 0 available")
            return None

        # Sort by level_required (ascending) - start from easiest
        def get_level(q: dict) -> int:
            level = q.get("level_required", "0")
            if isinstance(level, str):
                level = level.replace(",", "")
            return int(level)

        available.sort(key=get_level)

        best = available[0]
        logger.info(
            f"Selected quest: {best.get('title')} (ID: {best.get('id')}, "
            f"level: {best.get('level_required')}, success: {best.get('success_chance')}%)"
        )
        return best

    def _get_quest_points(self) -> tuple[int, int]:
        """
        Get current quest points.

        Returns:
            Tuple of (current_points, max_points).
        """
        info = self.client.get_player_info()
        if not info:
            return 0, 0

        current = info.get("quest_points", "0")
        maximum = info.get("max_quest_points", "0")

        # Parse string values
        if isinstance(current, str):
            current = int(current.replace(",", ""))
        if isinstance(maximum, str):
            maximum = int(maximum.replace(",", ""))

        return current, maximum

    def run_quests(self) -> QuestStats:
        """
        Run quest automation until quest points are depleted.

        Returns:
            Quest statistics.
        """
        self._running = True
        self.stats = QuestStats()

        logger.info("Starting quest automation...")

        # Get initial quest points
        current_qp, max_qp = self._get_quest_points()
        logger.info(f"Quest Points: {current_qp}/{max_qp}")

        if current_qp == 0:
            logger.warning("No quest points available!")
            return self.stats

        # Get quests and signed endpoint
        quests, get_endpoint, perform_endpoint = self.client.get_quests()

        if not quests or not perform_endpoint:
            logger.error("Failed to get quests or perform endpoint")
            return self.stats

        while self._running and current_qp > 0:
            try:
                # Select best quest
                quest = self._select_best_quest(quests)
                if not quest:
                    logger.info("No more suitable quests available")
                    break

                quest_id = quest.get("id")
                quest_title = quest.get("title", "Unknown")

                logger.info(f"📜 Performing quest: {quest_title}")

                # Perform quest
                result = self.client.perform_quest(quest_id, perform_endpoint)
                self.stats.quests_attempted += 1
                self.stats.quest_points_used += 1

                if result.get("success"):
                    self.stats.quests_succeeded += 1
                    self.stats.gold_earned += result.get("gold", 0)
                    self.stats.exp_earned += result.get("experience", 0)
                else:
                    self.stats.quests_failed += 1

                # Update quest points
                current_qp -= 1

                # Log progress
                logger.info(
                    f"Progress: {self.stats.quests_attempted} quests | "
                    f"QP: {current_qp}/{max_qp} | "
                    f"Gold: {self.stats.gold_earned} | EXP: {self.stats.exp_earned}"
                )

                # Human-like delay between quests
                if current_qp > 0:
                    human_delay(base=1.5, std=0.3, min_delay=1.0)

                # Refresh quests list periodically (every 10 quests) to get updated data
                if self.stats.quests_attempted % 10 == 0:
                    quests, _, new_endpoint = self.client.get_quests()
                    if new_endpoint:
                        perform_endpoint = new_endpoint

            except KeyboardInterrupt:
                logger.info("Interrupted by user")
                break
            except Exception as e:
                self.stats.errors += 1
                logger.error(f"Unexpected error: {e}")
                time.sleep(5)

        self._running = False
        logger.info(f"Quest session ended\n{self.stats}")

        return self.stats
