"""Captcha solver using Google Gemini Vision API."""

import re
import time
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
import google.generativeai as genai

from .config import Settings

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Solves SimpleMMO captcha using Google Gemini Vision."""

    # URLs for captcha page and images
    CAPTCHA_PAGE_URL = "https://web.simple-mmo.com/i-am-not-a-bot"
    CAPTCHA_IMAGE_URL = "https://web.simple-mmo.com/i-am-not-a-bot/generate_image?uid={}"

    # Browser-like headers
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, settings: Settings) -> None:
        """Initialize captcha solver with Gemini API."""
        self.settings = settings
        genai.configure(api_key=settings.gemini_api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

        # Setup cookies for web authentication (Laravel app)
        cookies = {}
        if settings.simplemmo_laravel_session:
            cookies["laravelsession"] = settings.simplemmo_laravel_session
            logger.info(f"Laravel session cookie set (length: {len(settings.simplemmo_laravel_session)})")
        if settings.simplemmo_xsrf_token:
            cookies["XSRF-TOKEN"] = settings.simplemmo_xsrf_token
            logger.info(f"XSRF token cookie set (length: {len(settings.simplemmo_xsrf_token)})")

        if not cookies:
            logger.warning("No session cookies configured! Captcha solving will likely fail.")

        self._http_client = httpx.Client(
            timeout=30.0,
            headers=self.HEADERS,
            cookies=cookies,
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close resources."""
        self._http_client.close()

    def __enter__(self) -> "CaptchaSolver":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def download_image(self, url: str) -> Image.Image | None:
        """Download image from URL."""
        try:
            response = self._http_client.get(url)
            response.raise_for_status()

            # Debug: log response info
            content_type = response.headers.get("content-type", "unknown")
            logger.debug(f"Image response: status={response.status_code}, type={content_type}, size={len(response.content)}")

            # Check if we got HTML instead of image (auth redirect)
            if "text/html" in content_type:
                logger.error(f"Got HTML instead of image - likely auth issue. First 200 chars: {response.text[:200]}")
                return None

            return Image.open(BytesIO(response.content))
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
            return None

    def _get_captcha_page(self) -> tuple[str | None, list[str] | None]:
        """
        Fetch the captcha page and extract prompt and image hashes.

        Returns:
            Tuple of (prompt, image_hashes) or (None, None) if failed.
            image_hashes is a list of 4 bcrypt hashes corresponding to images 0-3.
        """
        try:
            response = self._http_client.get(self.CAPTCHA_PAGE_URL)
            response.raise_for_status()
            html = response.text

            # Store for debugging
            self._last_captcha_html = html

            # Extract image hashes from onclick handlers
            # Pattern: chooseItem('$2y$10$...', false)
            hash_pattern = re.compile(r"chooseItem\('(\$2y\$10\$[^']+)',\s*false\)")
            matches = hash_pattern.findall(html)

            # Get first 4 unique hashes (the visible buttons)
            image_hashes = matches[:4] if len(matches) >= 4 else None

            if image_hashes:
                logger.debug(f"Found {len(image_hashes)} image hashes")
            else:
                logger.warning("Could not extract image hashes from page")

            # Extract prompt from the page
            prompt = None

            # Look for the specific div with text-2xl class containing the item name
            prompt_match = re.search(r'<div class="text-2xl[^"]*"[^>]*>([^<]+)</div>', html)
            if prompt_match:
                prompt = prompt_match.group(1).strip()

            if not prompt:
                patterns = [
                    r'text-2xl[^>]*font-semibold[^>]*>([^<]+)<',
                    r'Select the image[^<]*that shows[^<]*?([^<]+)',
                ]
                for pattern in patterns:
                    match = re.search(pattern, html, re.IGNORECASE)
                    if match:
                        prompt = match.group(1).strip()
                        if prompt:
                            break

            if prompt:
                logger.info(f"Captcha prompt: {prompt}")
            else:
                logger.warning("Could not extract captcha prompt from page")

            return prompt, image_hashes

        except Exception as e:
            logger.error(f"Error fetching captcha page: {e}")
            return None, None

    def solve_captcha(self) -> tuple[int | None, str | None]:
        """
        Solve the SimpleMMO captcha.

        Returns:
            Tuple of (answer 1-4, prompt text) or (None, None) if failed.
        """
        logger.info("Starting captcha solving process...")

        # Get the prompt and image hashes
        prompt, image_hashes = self._get_captcha_page()

        # Store hashes for submission
        self._image_hashes = image_hashes

        if not image_hashes:
            logger.error("Failed to extract image hashes from captcha page")
            return None, prompt

        if not prompt:
            prompt = "the item that is different from the others"

        # Download all 4 captcha images
        images = []
        for i in range(4):
            url = self.CAPTCHA_IMAGE_URL.format(i)
            logger.debug(f"Downloading captcha image {i+1}: {url}")
            img = self.download_image(url)
            if img is None:
                logger.error(f"Failed to download captcha image {i+1}")
                return None, prompt
            images.append(img)

        logger.info(f"Downloaded 4 captcha images, solving with prompt: {prompt}")

        # Solve using Gemini
        answer = self._solve_with_gemini(images, prompt)
        return answer, prompt

    def _solve_with_gemini(self, images: list[Image.Image], prompt: str) -> int | None:
        """
        Use Gemini to identify the correct image.

        Args:
            images: List of 4 PIL Images.
            prompt: The captcha prompt text.

        Returns:
            Index 1-4 of the correct image, or None if failed.
        """
        # Build the prompt for Gemini
        gemini_prompt = f"""You are solving a SimpleMMO captcha.

The task is: "{prompt}"

You will see 4 images numbered 1, 2, 3, 4.
Identify which ONE image matches the description or is different from the others.

IMPORTANT:
- Look carefully at each image
- Consider shapes, colors, objects shown
- Pick the image that best matches "{prompt}"

Respond with ONLY a single digit: 1, 2, 3, or 4
No explanation, just the number."""

        # Prepare content for Gemini
        content = [gemini_prompt]

        for i, img in enumerate(images, 1):
            content.append(f"\n\nImage {i}:")
            content.append(img)

        # Retry logic for rate limits (429 errors)
        max_retries = 3
        retry_delays = [30, 60, 120]  # seconds

        for attempt in range(max_retries + 1):
            try:
                # Send to Gemini
                response = self.model.generate_content(content)
                answer = response.text.strip()

                logger.debug(f"Gemini raw response: {answer}")

                # Parse response - expect single digit 1-4
                for char in answer:
                    if char in "1234":
                        result = int(char)
                        logger.info(f"Captcha solved: selected image {result}")
                        return result

                logger.warning(f"Could not parse Gemini response: {answer}")
                return None

            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "Resource exhausted" in error_str

                if is_rate_limit and attempt < max_retries:
                    delay = retry_delays[attempt]
                    logger.warning(f"Gemini rate limited, waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(delay)
                    continue

                logger.error(f"Error solving captcha with Gemini: {e}")
                return None

        return None

    def submit_captcha_answer(self, answer: int) -> bool:
        """
        Submit the captcha answer to the bot verification API.

        Args:
            answer: The selected image (1-4).

        Returns:
            True if submission was successful, False otherwise.
        """
        try:
            # The answer is 0-indexed (0, 1, 2, 3)
            answer_index = answer - 1

            # Get the hash for the selected image
            if not hasattr(self, '_image_hashes') or not self._image_hashes:
                logger.error("No image hashes available for submission")
                return False

            if answer_index < 0 or answer_index >= len(self._image_hashes):
                logger.error(f"Invalid answer index: {answer_index}")
                return False

            selected_hash = self._image_hashes[answer_index]
            logger.debug(f"Selected hash for image {answer}: {selected_hash[:30]}...")

            # Build JSON payload matching the browser request
            import random
            payload = {
                "data": selected_hash,
                "x": random.randint(500, 700),
                "y": random.randint(300, 500),
                "valid": False,
            }

            # Submit to the bot verification API
            submit_url = "https://web.simple-mmo.com/api/bot-verification"

            response = self._http_client.post(
                submit_url,
                json=payload,
                headers={
                    **self.HEADERS,
                    "Accept": "*/*",
                    "Content-Type": "application/json",
                    "Origin": "https://web.simple-mmo.com",
                    "Referer": f"{self.CAPTCHA_PAGE_URL}?new_page=true",
                },
            )

            logger.debug(f"Captcha submission response: status={response.status_code}")

            if response.status_code == 200:
                try:
                    result = response.json()
                    logger.debug(f"Captcha API response: {result}")

                    # Check for success in response
                    if result.get("success") or result.get("result") == "success":
                        logger.info("Captcha verified successfully!")
                        return True
                    elif result.get("error") or result.get("result") == "error":
                        logger.warning(f"Captcha verification failed: {result}")
                        return False
                    else:
                        # Assume success if no error
                        logger.info("Captcha submission completed")
                        return True
                except Exception:
                    # If response is not JSON, check text
                    if "success" in response.text.lower():
                        logger.info("Captcha verified successfully!")
                        return True
                    logger.info("Captcha submission completed (non-JSON response)")
                    return True
            else:
                logger.warning(f"Captcha submission returned status {response.status_code}")
                logger.debug(f"Response: {response.text[:500]}")
                return False

        except Exception as e:
            logger.error(f"Error submitting captcha answer: {e}")
            return False

    # Legacy methods for backwards compatibility
    def solve_from_urls(self, image_urls: list[str]) -> int | None:
        """Solve captcha from image URLs."""
        if len(image_urls) != 4:
            logger.error(f"Expected 4 images, got {len(image_urls)}")
            return None

        images = []
        for url in image_urls:
            img = self.download_image(url)
            if img is None:
                return None
            images.append(img)

        return self._solve_with_gemini(images, "the item that is different")

    def solve_from_images(self, images: list[Image.Image]) -> int | None:
        """Solve captcha from PIL Images."""
        if len(images) != 4:
            logger.error(f"Expected 4 images, got {len(images)}")
            return None
        return self._solve_with_gemini(images, "the item that is different")
