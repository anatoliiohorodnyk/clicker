"""Captcha solver using Google Gemini Vision API."""

import re
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

    def _get_captcha_page(self) -> tuple[str | None, str | None, str | None]:
        """
        Fetch the captcha page and extract prompt, CSRF token, and form action.

        Returns:
            Tuple of (prompt, csrf_token, form_action) or (None, None, None) if failed.
        """
        try:
            response = self._http_client.get(self.CAPTCHA_PAGE_URL)
            response.raise_for_status()
            html = response.text

            # Store for debugging
            self._last_captcha_html = html

            # Extract CSRF token
            csrf_match = re.search(r'name="_token"\s+value="([^"]+)"', html)
            if not csrf_match:
                csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
            csrf_token = csrf_match.group(1) if csrf_match else None
            if csrf_token:
                logger.debug(f"Found CSRF token: {csrf_token[:20]}...")

            # Extract form action
            form_match = re.search(r'<form[^>]*action="([^"]+)"[^>]*>', html)
            form_action = form_match.group(1) if form_match else None
            if form_action:
                logger.debug(f"Found form action: {form_action}")

            # Look for onclick handlers on images (common in captcha)
            onclick_match = re.search(r'onclick="[^"]*(/i-am-not-a-bot[^"\']*)"', html)
            if onclick_match and not form_action:
                form_action = onclick_match.group(1)
                logger.debug(f"Found onclick action: {form_action}")

            # Extract prompt from the page
            prompt = None
            patterns = [
                r'Select the image[^<]*that shows[^<]*?([^<]+)',
                r'text-2xl[^>]*>([^<]+)<',
                r'<h[12][^>]*>([^<]*Select[^<]*)<',
            ]

            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    prompt = match.group(1).strip()
                    if prompt:
                        logger.info(f"Captcha prompt: {prompt}")
                        break

            if not prompt:
                heading_match = re.search(r'<h\d[^>]*class="[^"]*text-2xl[^"]*"[^>]*>([^<]+)', html)
                if heading_match:
                    prompt = heading_match.group(1).strip()

            if not prompt:
                logger.warning("Could not extract captcha prompt from page")

            return prompt, csrf_token, form_action

        except Exception as e:
            logger.error(f"Error fetching captcha page: {e}")
            return None, None, None

    def solve_captcha(self) -> tuple[int | None, str | None]:
        """
        Solve the SimpleMMO captcha.

        Returns:
            Tuple of (answer 1-4, prompt text) or (None, None) if failed.
        """
        logger.info("Starting captcha solving process...")

        # Get the prompt, CSRF token, and form action
        prompt, csrf_token, form_action = self._get_captcha_page()

        # Store for submission
        self._csrf_token = csrf_token
        self._form_action = form_action

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
        try:
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
            logger.error(f"Error solving captcha with Gemini: {e}")
            return None

    def submit_captcha_answer(self, answer: int) -> bool:
        """
        Submit the captcha answer.

        Args:
            answer: The selected image (1-4).

        Returns:
            True if submission was successful, False otherwise.
        """
        try:
            # The answer is 0-indexed for the API (0, 1, 2, 3)
            answer_index = answer - 1

            # Build form data
            data = {
                "answer": str(answer_index),
            }

            # Add CSRF token if available
            if hasattr(self, '_csrf_token') and self._csrf_token:
                data["_token"] = self._csrf_token
                logger.debug("Including CSRF token in submission")

            # Determine submission URL
            if hasattr(self, '_form_action') and self._form_action:
                if self._form_action.startswith('http'):
                    submit_url = self._form_action
                else:
                    submit_url = f"https://web.simple-mmo.com{self._form_action}"
                logger.debug(f"Using form action URL: {submit_url}")
            else:
                # Try POST to the same page (common Laravel pattern)
                submit_url = self.CAPTCHA_PAGE_URL
                logger.debug(f"Using captcha page URL: {submit_url}")

            response = self._http_client.post(
                submit_url,
                data=data,
                headers={
                    **self.HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": self.CAPTCHA_PAGE_URL,
                },
            )

            logger.debug(f"Captcha submission response: status={response.status_code}, url={response.url}")

            # Check if successful - could be 200 or redirect (302/303)
            if response.status_code in (200, 302, 303):
                # Check if redirected away from captcha page (success)
                if "i-am-not-a-bot" not in str(response.url):
                    logger.info("Captcha answer submitted successfully (redirected)")
                    return True
                # Check response content for success indicators
                if "success" in response.text.lower() or "verified" in response.text.lower():
                    logger.info("Captcha answer submitted successfully")
                    return True
                # Check if still on captcha page (failure)
                if "i-am-not-a-bot" in response.text:
                    logger.warning("Still on captcha page after submission - answer may be wrong")
                    return False
                logger.info("Captcha submission completed")
                return True
            else:
                logger.warning(f"Captcha submission returned status {response.status_code}")
                # Log response for debugging
                logger.debug(f"Response content: {response.text[:500]}")
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
