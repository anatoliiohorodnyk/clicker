"""HTTP client for SimpleMMO API."""

import random
import logging
from typing import Any
from dataclasses import dataclass

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class TravelResult:
    """Result of a travel step."""

    success: bool
    action: str  # step, npc, item, material, text, etc.
    message: str
    data: dict[str, Any]
    wait_time: int  # seconds to wait before next step
    captcha_required: bool = False
    raw_response: dict[str, Any] | None = None


class SimpleMMOClient:
    """HTTP client for SimpleMMO API interactions."""

    # Browser-like headers to avoid detection
    DEFAULT_HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://web.simple-mmo.com",
        "Referer": "https://web.simple-mmo.com/travel",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }

    def __init__(self, settings: Settings) -> None:
        """Initialize client with settings."""
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.api_base_url,
            headers=self.DEFAULT_HEADERS,
            timeout=30.0,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "SimpleMMOClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _generate_coordinates(self) -> tuple[int, int]:
        """Generate random cursor coordinates to simulate human behavior."""
        d_1 = random.randint(750, 799)
        d_2 = random.randint(100, 299)
        return d_1, d_2

    def travel_step(self) -> TravelResult:
        """
        Perform a single travel step.

        Returns:
            TravelResult with step outcome and next action details.
        """
        d_1, d_2 = self._generate_coordinates()

        data = {
            "api_token": self.settings.simplemmo_api_token,
            "d_1": str(d_1),
            "d_2": str(d_2),
        }

        try:
            response = self._client.post(
                self.settings.travel_endpoint,
                data=data,
            )
            response.raise_for_status()
            result = response.json()

            logger.debug(f"Travel response: {result}")

            return self._parse_travel_response(result)

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during travel: {e}")
            return TravelResult(
                success=False,
                action="error",
                message=f"HTTP error: {e.response.status_code}",
                data={},
                wait_time=60,
            )
        except Exception as e:
            logger.error(f"Error during travel: {e}")
            return TravelResult(
                success=False,
                action="error",
                message=str(e),
                data={},
                wait_time=60,
            )

    def _parse_travel_response(self, response: dict[str, Any]) -> TravelResult:
        """Parse the travel API response into structured result."""
        # Check for captcha requirement
        text = response.get("text", "")
        if "human verification" in text.lower() or "verify" in text.lower():
            return TravelResult(
                success=False,
                action="captcha",
                message="Captcha verification required",
                data=response,
                wait_time=0,
                captcha_required=True,
                raw_response=response,
            )

        # Extract common fields
        wait_time = response.get("wait_time", response.get("nextwait", 5))

        # Determine action type based on response
        if response.get("npc_id") or response.get("is_npc"):
            return TravelResult(
                success=True,
                action="npc",
                message=text or "NPC encountered",
                data={
                    "npc_id": response.get("npc_id"),
                    "npc_name": response.get("npc_name"),
                    "level": response.get("level"),
                },
                wait_time=wait_time,
                raw_response=response,
            )

        if response.get("material") or response.get("material_id"):
            return TravelResult(
                success=True,
                action="material",
                message=text or "Material found",
                data={
                    "material_id": response.get("material_id"),
                    "material_name": response.get("material_name", response.get("material")),
                },
                wait_time=wait_time,
                raw_response=response,
            )

        if response.get("item") or response.get("item_id"):
            return TravelResult(
                success=True,
                action="item",
                message=text or "Item found",
                data={
                    "item_id": response.get("item_id"),
                    "item_name": response.get("item_name", response.get("item")),
                },
                wait_time=wait_time,
                raw_response=response,
            )

        if response.get("gold"):
            return TravelResult(
                success=True,
                action="gold",
                message=text or f"Found {response.get('gold')} gold",
                data={"gold": response.get("gold")},
                wait_time=wait_time,
                raw_response=response,
            )

        if response.get("exp") or response.get("xp"):
            return TravelResult(
                success=True,
                action="exp",
                message=text or f"Gained {response.get('exp', response.get('xp'))} XP",
                data={"exp": response.get("exp", response.get("xp"))},
                wait_time=wait_time,
                raw_response=response,
            )

        # Default: regular step
        return TravelResult(
            success=True,
            action="step",
            message=text or "Step taken",
            data=response,
            wait_time=wait_time,
            raw_response=response,
        )

    def attack_npc(self, npc_id: int) -> dict[str, Any]:
        """
        Attack an NPC during travel.

        Args:
            npc_id: The NPC identifier to attack.

        Returns:
            Attack result dictionary.
        """
        d_1, d_2 = self._generate_coordinates()

        data = {
            "api_token": self.settings.simplemmo_api_token,
            "npc_id": str(npc_id),
            "d_1": str(d_1),
            "d_2": str(d_2),
        }

        try:
            response = self._client.post(
                "/api/npc/attack",
                data=data,
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"NPC attack response: {result}")
            return result

        except Exception as e:
            logger.error(f"Error attacking NPC: {e}")
            return {"success": False, "error": str(e)}

    def gather_material(self, material_id: int) -> dict[str, Any]:
        """
        Gather a material during travel.

        Args:
            material_id: The material identifier to gather.

        Returns:
            Gather result dictionary.
        """
        d_1, d_2 = self._generate_coordinates()

        data = {
            "api_token": self.settings.simplemmo_api_token,
            "material_id": str(material_id),
            "d_1": str(d_1),
            "d_2": str(d_2),
        }

        try:
            response = self._client.post(
                "/api/material/gather",
                data=data,
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Material gather response: {result}")
            return result

        except Exception as e:
            logger.error(f"Error gathering material: {e}")
            return {"success": False, "error": str(e)}
