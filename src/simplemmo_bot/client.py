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

                logger.debug(f"Attack #{attack_count}: player_hp={result.get('player_hp')}, opponent_hp={result.get('opponent_hp')}")

                # Store the latest result
                final_result = result

                # Check if battle is finished
                opponent_hp = result.get("opponent_hp", 0)
                player_hp = result.get("player_hp", 0)
                battle_result = result.get("result")

                if opponent_hp <= 0:
                    # Victory!
                    final_result["win"] = True
                    logger.info(f"NPC defeated after {attack_count} attacks!")
                    break
                elif player_hp <= 0 or battle_result is not None:
                    # Defeat or battle ended
                    final_result["win"] = False
                    logger.info(f"Lost to NPC after {attack_count} attacks")
                    break

                # Small delay between attacks (0.3-0.6 seconds)
                import time
                time.sleep(0.3 + random.random() * 0.3)

            return final_result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error attacking NPC {npc_id}: {e.response.status_code}")
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
        import time

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
            max_gathers = 50  # Safety limit
            total_player_exp = 0
            total_skill_exp = 0
            final_result: dict[str, Any] = {}

            while gather_count < max_gathers:
                gather_count += 1

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

                logger.debug(f"Gather #{gather_count}: type={result.get('type')}, is_end={result.get('is_end')}")

                # Accumulate experience
                total_player_exp += result.get("player_experience_gained", 0)
                total_skill_exp += result.get("skill_experience_gained", 0)

                # Store the latest result
                final_result = result

                # Check if gathering is finished
                if result.get("is_end", False):
                    logger.info(f"Gathered {gather_count}x material! Total: +{total_player_exp} XP, +{total_skill_exp} skill XP")
                    break

                # Check for errors
                if result.get("type") != "success":
                    logger.warning(f"Gather returned non-success: {result.get('type')}")
                    break

                # Small delay between gathers (0.3-0.6 seconds)
                time.sleep(0.3 + random.random() * 0.3)

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
