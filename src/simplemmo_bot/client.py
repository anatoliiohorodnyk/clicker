"""HTTP client for SimpleMMO API."""

import re
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
    action: str  # step, npc, item, material, text, rate_limit, etc.
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

    # Regex patterns for parsing HTML responses
    NPC_ATTACK_PATTERN = re.compile(r'/npcs/attack/(\d+)')
    NPC_SPRITE_PATTERN = re.compile(r"/img/sprites/enemies/(\d+)\.png")
    MATERIAL_GATHER_PATTERN = re.compile(r'/crafting/material/gather/(\d+)')
    ITEM_PATTERN = re.compile(r'/item/(\d+)')

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
        text = response.get("text", "")
        text_lower = text.lower()

        # Extract wait time from response
        wait_time = response.get("wait_time", response.get("nextwait", 5))
        if isinstance(wait_time, str):
            try:
                wait_time = int(wait_time)
            except ValueError:
                wait_time = 5

        # Note: "Woah steady on there" is just flavor text, not rate limiting

        # Check for captcha/verification requirement
        if "human verification" in text_lower or "verify" in text_lower or "i-am-not-a-bot" in text_lower:
            return TravelResult(
                success=False,
                action="captcha",
                message="Captcha verification required",
                data=response,
                wait_time=0,
                captcha_required=True,
                raw_response=response,
            )

        # Check for death
        if "you're dead" in text_lower or "need to heal yourself" in text_lower:
            return TravelResult(
                success=False,
                action="dead",
                message="Character is dead - need to respawn",
                data={},
                wait_time=300,  # 5 minutes respawn
                raw_response=response,
            )

        # Check for NPC encounter - parse from HTML
        npc_match = self.NPC_ATTACK_PATTERN.search(text)
        if npc_match:
            npc_id = int(npc_match.group(1))
            # Try to extract NPC name from text
            npc_name = self._extract_text_content(text)
            return TravelResult(
                success=True,
                action="npc",
                message=f"NPC encountered: {npc_name}" if npc_name else "NPC encountered",
                data={
                    "npc_id": npc_id,
                    "npc_name": npc_name,
                },
                wait_time=wait_time,
                raw_response=response,
            )

        # Also check for NPC by sprite image (backup detection)
        sprite_match = self.NPC_SPRITE_PATTERN.search(text)
        if sprite_match and "attack" in text_lower:
            # Try to find the attack link
            npc_match = self.NPC_ATTACK_PATTERN.search(text)
            if npc_match:
                npc_id = int(npc_match.group(1))
                return TravelResult(
                    success=True,
                    action="npc",
                    message="NPC encountered",
                    data={"npc_id": npc_id},
                    wait_time=wait_time,
                    raw_response=response,
                )

        # Check for material - parse from HTML
        material_match = self.MATERIAL_GATHER_PATTERN.search(text)
        if material_match:
            material_id = int(material_match.group(1))
            material_name = self._extract_text_content(text)
            return TravelResult(
                success=True,
                action="material",
                message=f"Material found: {material_name}" if material_name else "Material found",
                data={
                    "material_id": material_id,
                    "material_name": material_name,
                },
                wait_time=wait_time,
                raw_response=response,
            )

        # Check for item
        item_match = self.ITEM_PATTERN.search(text)
        if item_match:
            item_id = int(item_match.group(1))
            item_name = self._extract_text_content(text)
            return TravelResult(
                success=True,
                action="item",
                message=f"Item found: {item_name}" if item_name else "Item found",
                data={
                    "item_id": item_id,
                    "item_name": item_name,
                },
                wait_time=wait_time,
                raw_response=response,
            )

        # Check for gold (from JSON fields)
        if response.get("gold"):
            return TravelResult(
                success=True,
                action="gold",
                message=f"Found {response.get('gold')} gold",
                data={"gold": response.get("gold")},
                wait_time=wait_time,
                raw_response=response,
            )

        # Check for XP (from JSON fields)
        if response.get("exp") or response.get("xp"):
            exp = response.get("exp", response.get("xp"))
            return TravelResult(
                success=True,
                action="exp",
                message=f"Gained {exp} XP",
                data={"exp": exp},
                wait_time=wait_time,
                raw_response=response,
            )

        # Default: regular step with flavor text
        clean_text = self._extract_text_content(text)
        return TravelResult(
            success=True,
            action="step",
            message=clean_text[:100] if clean_text else "Step taken",
            data=response,
            wait_time=wait_time,
            raw_response=response,
        )

    def _extract_text_content(self, html: str) -> str:
        """Extract readable text content from HTML, removing tags."""
        if not html:
            return ""
        # Remove HTML tags
        clean = re.sub(r'<[^>]+>', ' ', html)
        # Remove extra whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()
        return clean

    def attack_npc(self, npc_id: int) -> dict[str, Any]:
        """
        Attack an NPC during travel.

        Args:
            npc_id: The NPC identifier to attack.

        Returns:
            Attack result dictionary.
        """
        d_1, d_2 = self._generate_coordinates()

        # First, navigate to the NPC attack page
        try:
            # Get the attack page
            attack_url = f"/npcs/attack/{npc_id}"
            response = self._client.get(
                attack_url.replace("/npcs", ""),
                headers={
                    **self.DEFAULT_HEADERS,
                    "Referer": "https://web.simple-mmo.com/travel",
                },
            )

            # Now perform the actual attack
            data = {
                "api_token": self.settings.simplemmo_api_token,
                "npc_id": str(npc_id),
                "d_1": str(d_1),
                "d_2": str(d_2),
            }

            response = self._client.post(
                "/api/npcs/attack",
                data=data,
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"NPC attack response: {result}")
            return result

        except Exception as e:
            logger.error(f"Error attacking NPC {npc_id}: {e}")
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
                "/api/crafting/material/gather",
                data=data,
            )
            response.raise_for_status()
            result = response.json()
            logger.debug(f"Material gather response: {result}")
            return result

        except Exception as e:
            logger.error(f"Error gathering material {material_id}: {e}")
            return {"success": False, "error": str(e)}
