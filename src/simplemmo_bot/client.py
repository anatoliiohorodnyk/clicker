"""HTTP client for SimpleMMO API."""

import re
import time
import random
import logging
from typing import Any
from dataclasses import dataclass

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class InventoryItem:
    """Represents an item in the inventory."""

    id: int
    name: str
    item_type: str  # Weapon, Helmet, Armour, Shield, Pet
    strength: int  # Attack stat
    defence: int  # Defence stat
    equippable: bool  # Has equip button (not currently equipped)


def human_delay(base: float = 1.0, std: float = 0.15, min_delay: float = 0.8) -> None:
    """
    Sleep for a human-like random duration using normal distribution.

    Args:
        base: Mean delay in seconds
        std: Standard deviation
        min_delay: Minimum delay (floor)
    """
    delay = max(min_delay, random.gauss(base, std))

    # 5% chance of a "thinking" pause (human got distracted)
    if random.random() < 0.05:
        delay += random.uniform(1.0, 3.0)

    time.sleep(delay)


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

    # Inventory parsing patterns
    INVENTORY_ITEM_BLOCK_PATTERN = re.compile(r'id="item-(\d+)-block"')
    INVENTORY_EQUIP_PATTERN = re.compile(r'/inventory/equip/(\d+)')
    INVENTORY_STAT_PATTERN = re.compile(r'<strong>\+(\d+)</strong>\s*(str|def)', re.IGNORECASE)
    INVENTORY_TYPE_PATTERN = re.compile(r'type%5B%5D=(Weapon|Helmet|Armour|Shield|Pet)', re.IGNORECASE)

    def __init__(self, settings: Settings) -> None:
        """Initialize client with settings."""
        self.settings = settings

        # Build cookies dict from settings
        cookies = {}
        if settings.simplemmo_laravel_session:
            cookies["laravelsession"] = settings.simplemmo_laravel_session
        if settings.simplemmo_xsrf_token:
            cookies["XSRF-TOKEN"] = settings.simplemmo_xsrf_token

        self._client = httpx.Client(
            base_url=settings.api_base_url,
            headers=self.DEFAULT_HEADERS,
            cookies=cookies if cookies else None,
            timeout=30.0,
            follow_redirects=False,  # Don't auto-follow redirects so we can detect auth issues
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

        # Always extract gold and exp from response (they can be in any response)
        gold = 0
        exp = 0

        # Check various possible field names for gold
        if response.get("gold_amount"):
            try:
                gold = int(response.get("gold_amount", 0))
            except (ValueError, TypeError):
                gold = 0
        elif response.get("gold"):
            try:
                gold = int(response.get("gold", 0))
            except (ValueError, TypeError):
                gold = 0

        # Check various possible field names for exp
        if response.get("exp_amount"):
            try:
                exp = int(response.get("exp_amount", 0))
            except (ValueError, TypeError):
                exp = 0
        elif response.get("exp"):
            try:
                exp = int(response.get("exp", 0))
            except (ValueError, TypeError):
                exp = 0
        elif response.get("xp"):
            try:
                exp = int(response.get("xp", 0))
            except (ValueError, TypeError):
                exp = 0

        # Log gold/exp for debugging
        if gold > 0 or exp > 0:
            logger.debug(f"Step rewards: gold={gold}, exp={exp}")

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
                    "gold": gold,
                    "exp": exp,
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
                    data={"npc_id": npc_id, "gold": gold, "exp": exp},
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
                    "gold": gold,
                    "exp": exp,
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
                    "gold": gold,
                    "exp": exp,
                },
                wait_time=wait_time,
                raw_response=response,
            )

        # Default: regular step with flavor text (include any gold/exp found)
        clean_text = self._extract_text_content(text)
        return TravelResult(
            success=True,
            action="step",
            message=clean_text[:100] if clean_text else "Step taken",
            data={"gold": gold, "exp": exp, **response},
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
            Attack result dictionary with win/loss, gold, exp.
        """
        try:
            # Step 1: Load the attack page to get the signed API URL
            attack_page_url = f"https://web.simple-mmo.com/npcs/attack/{npc_id}?new_page=true"
            logger.debug(f"Loading NPC attack page: {attack_page_url}")

            # Build headers with XSRF token if available
            page_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://web.simple-mmo.com/travel?new_page=true",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                page_headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            page_response = self._client.get(
                attack_page_url,
                headers=page_headers,
            )

            # Check for redirect (authentication issue)
            if page_response.status_code in (301, 302, 303, 307, 308):
                logger.error(f"Got redirect {page_response.status_code} - likely missing/expired session cookies")
                return {"success": False, "error": "Session expired - update SIMPLEMMO_LARAVEL_SESSION cookie"}

            page_response.raise_for_status()
            html = page_response.text

            # Step 2: Parse the attack API URL from the page
            # The URL is in JavaScript game_data as escaped JSON:
            # "npc.attack_endpoint":"https:\/\/web.simple-mmo.com\/api\/npcs\/attack\/434g3s?expires=...&signature=..."
            # Note: \/ is escaped slash, \u0026 is escaped &
            api_url_pattern = re.compile(
                r'"npc\.attack_endpoint"\s*:\s*"https?:\\?/\\?/web\.simple-mmo\.com\\?/api\\?/npcs\\?/attack\\?/([a-zA-Z0-9]+)\?expires=(\d+)(?:\\u0026|&)signature=([a-f0-9]+)"'
            )
            match = api_url_pattern.search(html)

            if not match:
                logger.error("Could not find attack API URL in page")
                logger.debug(f"Page content (first 2000 chars): {html[:2000]}")
                return {"success": False, "error": "Attack API URL not found"}

            api_code = match.group(1)
            expires = match.group(2)
            signature = match.group(3)

            attack_api_url = f"https://web.simple-mmo.com/api/npcs/attack/{api_code}?expires={expires}&signature={signature}"
            logger.debug(f"Found attack API URL: {attack_api_url}")

            # Step 3: Attack in a loop until battle is finished
            attack_payload = {
                "npc_id": npc_id,
                "special_attack": False,
            }

            attack_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.simplemmo_api_token}",
                "Origin": "https://web.simple-mmo.com",
                "Referer": attack_page_url,
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                attack_headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            # Battle loop - attack until someone's HP reaches 0
            attack_count = 0
            max_attacks = 50  # Safety limit
            final_result: dict[str, Any] = {}

            while attack_count < max_attacks:
                attack_count += 1

                attack_response = self._client.post(
                    attack_api_url,
                    json=attack_payload,
                    headers=attack_headers,
                )
                attack_response.raise_for_status()
                result = attack_response.json()

                logger.debug(f"Attack #{attack_count}: player_hp={result.get('player_hp')}, opponent_hp={result.get('opponent_hp')}, keys={list(result.keys())}")

                # Store the latest result
                final_result = result

                # Check if battle is finished
                # Handle None values (can happen if player dies mid-battle)
                opponent_hp = result.get("opponent_hp")
                player_hp = result.get("player_hp")
                battle_result = result.get("result")
                has_rewards = "rewards" in result and result["rewards"]

                # Battle ends when we have a result field or rewards
                if battle_result is not None or has_rewards:
                    if battle_result == "win" or (opponent_hp is not None and opponent_hp <= 0):
                        final_result["win"] = True
                        logger.info(f"NPC defeated after {attack_count} attacks!")
                    else:
                        final_result["win"] = False
                        logger.info(f"Lost to NPC after {attack_count} attacks")
                    break
                elif player_hp is not None and player_hp <= 0:
                    # Player died
                    final_result["win"] = False
                    logger.info(f"Lost to NPC after {attack_count} attacks")
                    break
                elif player_hp is None and opponent_hp is None:
                    # Both HP are None - likely an error state, check for death message
                    if result.get("message") or result.get("error"):
                        logger.warning(f"Battle ended unexpectedly: {result}")
                        final_result["win"] = False
                        break

                # Human-like delay between attacks
                human_delay(base=1.1, std=0.15, min_delay=0.8)

            return final_result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error attacking NPC {npc_id}: {e.response.status_code}")
            # Debug: show response body for 404 errors
            if e.response.status_code == 404:
                logger.debug(f"404 response body (first 500 chars): {e.response.text[:500]}")
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error attacking NPC {npc_id}: {e}")
            return {"success": False, "error": str(e)}

    def gather_material(self, material_id: int) -> dict[str, Any]:
        """
        Gather a material during travel.

        Uses the same two-step approach as NPC attacks:
        1. Load the gather page to get the signed API URL
        2. POST to the signed endpoint to gather

        Args:
            material_id: The material identifier to gather.

        Returns:
            Gather result dictionary with total exp gained and gather count.
        """
        try:
            # Step 1: Load the gather page to get the signed API URL
            gather_page_url = f"https://web.simple-mmo.com/crafting/material/gather/{material_id}?new_page=true"
            logger.debug(f"Loading material gather page: {gather_page_url}")

            page_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://web.simple-mmo.com/travel?new_page=true",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                page_headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            page_response = self._client.get(
                gather_page_url,
                headers=page_headers,
            )

            # Check for redirect (authentication issue)
            if page_response.status_code in (301, 302, 303, 307, 308):
                logger.error(f"Got redirect {page_response.status_code} - likely missing/expired session cookies")
                return {"success": False, "error": "Session expired - update SIMPLEMMO_LARAVEL_SESSION cookie"}

            page_response.raise_for_status()
            html = page_response.text

            # Step 2: Parse the gather API URL and session ID from game_data
            # Format: "gathering.gather_endpoint":"https:\/\/web.simple-mmo.com\/api\/crafting\/material\/gather?expires=...&signature=..."
            gather_endpoint_pattern = re.compile(
                r'"gathering\.gather_endpoint"\s*:\s*"(https?:\\?/\\?/web\.simple-mmo\.com\\?/api\\?/crafting\\?/material\\?/gather\?expires=\d+(?:\\u0026|&)signature=[a-f0-9]+)"'
            )
            endpoint_match = gather_endpoint_pattern.search(html)

            if not endpoint_match:
                logger.error("Could not find gather API URL in page")
                logger.debug(f"Page content (first 2000 chars): {html[:2000]}")
                return {"success": False, "error": "Gather API URL not found"}

            # Unescape the URL
            gather_api_url = endpoint_match.group(1)
            gather_api_url = gather_api_url.replace("\\/", "/").replace("\\u0026", "&")
            logger.debug(f"Found gather API URL: {gather_api_url}")

            # Parse material_session_id
            session_id_pattern = re.compile(r'"gathering\.material_session_id"\s*:\s*(\d+)')
            session_match = session_id_pattern.search(html)

            if not session_match:
                logger.error("Could not find material_session_id in page")
                return {"success": False, "error": "Material session ID not found"}

            material_session_id = int(session_match.group(1))
            logger.debug(f"Material session ID: {material_session_id}")

            # Parse available amount (optional, for logging)
            amount_pattern = re.compile(r'"gathering\.available_amount"\s*:\s*(\d+)')
            amount_match = amount_pattern.search(html)
            available_amount = int(amount_match.group(1)) if amount_match else 1
            logger.debug(f"Available amount: {available_amount}")

            # Step 3: Gather in a loop until is_end is true
            gather_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.simplemmo_api_token}",
                "Origin": "https://web.simple-mmo.com",
                "Referer": gather_page_url,
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                gather_headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            # Gather loop
            gather_count = 0
            iteration = 0
            max_iterations = 50  # Safety limit
            total_player_exp = 0
            total_skill_exp = 0
            final_result: dict[str, Any] = {}

            while iteration < max_iterations:
                iteration += 1

                gather_payload = {
                    "quantity": 1,
                    "id": material_session_id,
                }

                gather_response = self._client.post(
                    gather_api_url,
                    json=gather_payload,
                    headers=gather_headers,
                )
                gather_response.raise_for_status()
                result = gather_response.json()

                logger.debug(f"Gather iteration {iteration}: type={result.get('type')}, is_end={result.get('is_end')}")

                # Only count successful gathers
                if result.get("type") == "success":
                    gather_count += 1
                    total_player_exp += result.get("player_experience_gained", 0)
                    total_skill_exp += result.get("skill_experience_gained", 0)

                # Store the latest result
                final_result = result

                # Check if gathering is finished
                if result.get("is_end", False):
                    logger.info(f"Gathered {gather_count}x material! Total: +{total_player_exp} XP, +{total_skill_exp} skill XP")
                    break

                # Stop on error
                if result.get("type") != "success":
                    logger.warning(f"Gather failed: {result}")
                    break

                # Human-like delay between gathers
                human_delay(base=1.15, std=0.1, min_delay=1.0)

            # Add totals to final result
            final_result["total_player_exp"] = total_player_exp
            final_result["total_skill_exp"] = total_skill_exp
            final_result["gather_count"] = gather_count
            final_result["success"] = True

            return final_result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error gathering material {material_id}: {e.response.status_code}")
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error gathering material {material_id}: {e}")
            return {"success": False, "error": str(e)}

    def heal(self) -> dict[str, Any]:
        """
        Heal/respawn the character at the healer.

        Returns:
            Result dictionary with success status.
        """
        try:
            heal_url = "https://web.simple-mmo.com/api/healer/heal"

            headers = {
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Origin": "https://web.simple-mmo.com",
                "Referer": "https://web.simple-mmo.com/healer?new_page_refresh=true",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            response = self._client.post(
                heal_url,
                headers=headers,
                content="",  # Empty body
            )
            response.raise_for_status()
            result = response.json()

            if result.get("type") == "success":
                logger.info(f"💚 Healed: {result.get('result', 'Health restored')}")
                return {"success": True, "message": result.get("result")}
            else:
                logger.warning(f"Heal response: {result}")
                return {"success": False, "error": result.get("result", "Unknown error")}

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error healing: {e.response.status_code}")
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error healing: {e}")
            return {"success": False, "error": str(e)}

    def get_player_info(self) -> dict[str, Any]:
        """
        Get player information including quest points.

        Returns:
            Player info dictionary with quest_points, gold, level, etc.
        """
        try:
            url = "https://web.simple-mmo.com/api/web-app"

            headers = {
                "Accept": "*/*",
                "Referer": "https://web.simple-mmo.com/quests",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }

            response = self._client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Error getting player info: {e}")
            return {}

    def get_quests(self) -> tuple[list[dict], str | None, str | None]:
        """
        Get list of uncompleted quests.

        Returns:
            Tuple of (quests_list, get_endpoint, perform_endpoint).
            Endpoints are signed URLs for API calls.
        """
        try:
            # Step 1: Load quests page to get signed URLs
            quests_page_url = "https://web.simple-mmo.com/quests"
            logger.debug(f"Loading quests page: {quests_page_url}")

            page_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://web.simple-mmo.com/",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }

            page_response = self._client.get(quests_page_url, headers=page_headers)
            page_response.raise_for_status()
            html = page_response.text

            # Step 2: Parse signed URLs from page
            # Try multiple patterns - the format may vary

            # Pattern 1: Standard JSON format with escaped slashes
            # "quests.get_endpoint":"https:\/\/web.simple-mmo.com\/api\/quests\/get?expires=...&signature=..."
            get_patterns = [
                # Escaped slashes with \u0026
                r'"quests\.get_endpoint"\s*:\s*"(https?:[^"]+/api/quests/get\?[^"]+)"',
                # Any URL with /api/quests/get
                r'(https?://web\.simple-mmo\.com/api/quests/get\?expires=\d+&signature=[a-f0-9]+)',
                # Escaped format
                r'(https?:\\?/\\?/web\.simple-mmo\.com\\?/api\\?/quests\\?/get\?expires=\d+(?:\\u0026|&)signature=[a-f0-9]+)',
            ]

            perform_patterns = [
                r'"quests\.perform_endpoint"\s*:\s*"(https?:[^"]+/api/quests/perform\?[^"]+)"',
                r'(https?://web\.simple-mmo\.com/api/quests/perform\?expires=\d+&signature=[a-f0-9]+)',
                r'(https?:\\?/\\?/web\.simple-mmo\.com\\?/api\\?/quests\\?/perform\?expires=\d+(?:\\u0026|&)signature=[a-f0-9]+)',
            ]

            get_endpoint = None
            perform_endpoint = None

            # Try each pattern for get_endpoint
            for pattern in get_patterns:
                match = re.search(pattern, html)
                if match:
                    get_endpoint = match.group(1).replace("\\/", "/").replace("\\u0026", "&")
                    logger.debug(f"Found get_endpoint with pattern: {pattern[:50]}...")
                    break

            # Try each pattern for perform_endpoint
            for pattern in perform_patterns:
                match = re.search(pattern, html)
                if match:
                    perform_endpoint = match.group(1).replace("\\/", "/").replace("\\u0026", "&")
                    logger.debug(f"Found perform_endpoint with pattern: {pattern[:50]}...")
                    break

            if not get_endpoint:
                # Log more context to help debug
                logger.error("Could not find quests.get_endpoint in page")
                # Search for any 'quests' or 'endpoint' strings
                quests_mentions = re.findall(r'.{0,50}quests.{0,50}', html, re.IGNORECASE)[:5]
                logger.debug(f"Found 'quests' mentions: {quests_mentions}")
                endpoint_mentions = re.findall(r'.{0,50}endpoint.{0,50}', html, re.IGNORECASE)[:5]
                logger.debug(f"Found 'endpoint' mentions: {endpoint_mentions}")
                return [], None, None

            logger.debug(f"Found quests.get_endpoint: {get_endpoint}")
            logger.debug(f"Found quests.perform_endpoint: {perform_endpoint}")

            # Step 3: Fetch quests list
            quest_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.simplemmo_api_token}",
                "Origin": "https://web.simple-mmo.com",
                "Referer": quests_page_url,
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }

            quest_response = self._client.post(
                get_endpoint,
                json={"type": "NOT_COMPLETED"},
                headers=quest_headers,
            )
            quest_response.raise_for_status()
            result = quest_response.json()

            if result.get("status") == "success":
                quests = result.get("expeditions", [])
                logger.info(f"Found {len(quests)} uncompleted quests")
                return quests, get_endpoint, perform_endpoint
            else:
                logger.warning(f"Failed to get quests: {result}")
                return [], get_endpoint, perform_endpoint

        except Exception as e:
            logger.error(f"Error getting quests: {e}")
            return [], None, None

    def perform_quest(self, quest_id: int, perform_endpoint: str) -> dict[str, Any]:
        """
        Perform a quest.

        Args:
            quest_id: The quest/expedition ID.
            perform_endpoint: Signed API endpoint URL.

        Returns:
            Quest result dictionary.
        """
        try:
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.simplemmo_api_token}",
                "Origin": "https://web.simple-mmo.com",
                "Referer": "https://web.simple-mmo.com/quests",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }
            if self.settings.simplemmo_xsrf_token:
                headers["X-XSRF-TOKEN"] = self.settings.simplemmo_xsrf_token

            payload = {
                "expedition_id": quest_id,
                "quantity": 1,
            }

            response = self._client.post(
                perform_endpoint,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()

            if result.get("status") == "success":
                gold = result.get("gold", 0)
                exp = result.get("experience", 0)
                logger.info(f"Quest completed! +{exp} XP, +{gold} gold")
                return {"success": True, **result}
            else:
                logger.warning(f"Quest failed: {result}")
                return {"success": False, **result}

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error performing quest {quest_id}: {e.response.status_code}")
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error performing quest {quest_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_inventory(self) -> list[InventoryItem]:
        """
        Fetch and parse inventory items.

        Returns:
            List of InventoryItem objects that can be equipped.
        """
        try:
            inventory_url = "https://web.simple-mmo.com/inventory/items"
            logger.debug(f"Fetching inventory: {inventory_url}")

            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://web.simple-mmo.com/",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }

            response = self._client.get(inventory_url, headers=headers)

            # Check for redirect (authentication issue)
            if response.status_code in (301, 302, 303, 307, 308):
                logger.error(f"Got redirect {response.status_code} - session expired")
                return []

            response.raise_for_status()
            html = response.text

            return self._parse_inventory_html(html)

        except Exception as e:
            logger.error(f"Error fetching inventory: {e}")
            return []

    def _parse_inventory_html(self, html: str) -> list[InventoryItem]:
        """
        Parse inventory HTML to extract equippable items.

        Args:
            html: Raw HTML from /inventory/items page.

        Returns:
            List of InventoryItem objects.
        """
        items: list[InventoryItem] = []

        # Split HTML into item blocks using the item-X-block pattern
        # Each item block contains info about one item
        item_blocks = re.split(r'(?=id="item-\d+-block")', html)

        for block in item_blocks:
            # Find item ID from block id attribute
            id_match = self.INVENTORY_ITEM_BLOCK_PATTERN.search(block)
            if not id_match:
                continue

            item_id = int(id_match.group(1))

            # Check if item is equippable (has equip button)
            equip_match = self.INVENTORY_EQUIP_PATTERN.search(block)
            if not equip_match:
                continue  # Skip items without equip button (already equipped or not equippable)

            # Extract item type from URL
            type_match = self.INVENTORY_TYPE_PATTERN.search(block)
            item_type = type_match.group(1).capitalize() if type_match else "Unknown"

            # Extract stats
            strength = 0
            defence = 0
            for stat_match in self.INVENTORY_STAT_PATTERN.finditer(block):
                value = int(stat_match.group(1))
                stat_type = stat_match.group(2).lower()
                if stat_type == "str":
                    strength = value
                elif stat_type == "def":
                    defence = value

            # Try to extract item name from alt attribute or title
            name_match = re.search(r'alt="([^"]+)"', block)
            if not name_match:
                name_match = re.search(r'title="([^"]+)"', block)
            item_name = name_match.group(1) if name_match else f"Item #{item_id}"

            items.append(InventoryItem(
                id=item_id,
                name=item_name,
                item_type=item_type,
                strength=strength,
                defence=defence,
                equippable=True,
            ))

        logger.debug(f"Parsed {len(items)} equippable items from inventory")
        return items

    def equip_item(self, item_id: int) -> dict[str, Any]:
        """
        Equip an item by its ID.

        Args:
            item_id: The item ID to equip.

        Returns:
            Result dictionary with success status.
        """
        try:
            equip_url = f"https://web.simple-mmo.com/inventory/equip/{item_id}"
            logger.debug(f"Equipping item {item_id}: {equip_url}")

            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://web.simple-mmo.com/inventory/items",
                "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
            }

            response = self._client.get(equip_url, headers=headers)

            # Check for redirect (often means success, redirects back to inventory)
            if response.status_code in (301, 302, 303, 307, 308):
                logger.info(f"Item {item_id} equipped successfully (redirect)")
                return {"success": True, "item_id": item_id}

            response.raise_for_status()

            # Check response for success indicators
            if "equipped" in response.text.lower() or response.status_code == 200:
                logger.info(f"Item {item_id} equipped successfully")
                return {"success": True, "item_id": item_id}

            logger.warning(f"Unexpected response when equipping item {item_id}")
            return {"success": False, "error": "Unexpected response"}

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error equipping item {item_id}: {e.response.status_code}")
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Error equipping item {item_id}: {e}")
            return {"success": False, "error": str(e)}

    def equip_best_items(self) -> dict[str, Any]:
        """
        Find and equip the best items from inventory for each equipment slot.

        Best items are determined by:
        - Weapons: highest strength (str)
        - Helmet, Armour, Shield: highest defence (def)
        - Pet: highest combined stats

        Returns:
            Result dictionary with equipped items count and details.
        """
        try:
            items = self.get_inventory()
            if not items:
                logger.info("No equippable items found in inventory")
                return {"success": True, "equipped": 0, "items": []}

            # Group items by type
            items_by_type: dict[str, list[InventoryItem]] = {}
            for item in items:
                if item.item_type not in items_by_type:
                    items_by_type[item.item_type] = []
                items_by_type[item.item_type].append(item)

            equipped_items: list[dict[str, Any]] = []

            # Find best item for each slot
            for item_type, type_items in items_by_type.items():
                if not type_items:
                    continue

                # Determine which stat to prioritize
                if item_type.lower() == "weapon":
                    # Weapons: prioritize strength
                    best_item = max(type_items, key=lambda x: x.strength)
                elif item_type.lower() == "pet":
                    # Pets: prioritize combined stats
                    best_item = max(type_items, key=lambda x: x.strength + x.defence)
                else:
                    # Armor (Helmet, Armour, Shield): prioritize defence
                    best_item = max(type_items, key=lambda x: x.defence)

                logger.info(
                    f"Best {item_type}: {best_item.name} "
                    f"(+{best_item.strength} str, +{best_item.defence} def)"
                )

                # Equip the best item
                result = self.equip_item(best_item.id)
                if result.get("success"):
                    equipped_items.append({
                        "id": best_item.id,
                        "name": best_item.name,
                        "type": item_type,
                        "strength": best_item.strength,
                        "defence": best_item.defence,
                    })

                # Human-like delay between equips
                human_delay(base=0.8, std=0.1, min_delay=0.5)

            logger.info(f"Equipped {len(equipped_items)} best items")
            return {
                "success": True,
                "equipped": len(equipped_items),
                "items": equipped_items,
            }

        except Exception as e:
            logger.error(f"Error equipping best items: {e}")
            return {"success": False, "error": str(e), "equipped": 0, "items": []}
