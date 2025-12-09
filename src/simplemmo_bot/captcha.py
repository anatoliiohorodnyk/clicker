"""Captcha solver using AI Vision APIs (Gemini or OpenAI-compatible)."""

import re
import time
import base64
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from .config import Settings

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Solves SimpleMMO captcha using AI Vision APIs."""

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

    # Captcha solving prompt
    CAPTCHA_PROMPT = """You are solving a SimpleMMO captcha.

The task is: "{prompt}"

You will see 4 images numbered 1, 2, 3, 4.
Identify which ONE image matches the description or is different from the others.

IMPORTANT:
- Look carefully at each image
- Consider shapes, colors, objects shown
- Pick the image that best matches "{prompt}"

Respond with ONLY a single digit: 1, 2, 3, or 4
No explanation, just the number."""

    def __init__(self, settings: Settings) -> None:
        """Initialize captcha solver with AI API."""
        self.settings = settings
        self._quota_exhausted_until: float = 0

        # Determine which provider to use
        self.provider = getattr(settings, 'captcha_provider', 'gemini').lower()

        if self.provider == 'cloudflare':
            self._init_cloudflare(settings)
        elif self.provider == 'openai':
            self._init_openai(settings)
        else:
            self._init_gemini(settings)

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

    def _init_gemini(self, settings: Settings) -> None:
        """Initialize Gemini API."""
        import google.generativeai as genai

        genai.configure(api_key=settings.gemini_api_key)
        model_name = getattr(settings, 'gemini_model', 'gemini-2.0-flash')
        self.gemini_model = genai.GenerativeModel(model_name)
        logger.info(f"Using Gemini provider with model: {model_name}")

    def _init_cloudflare(self, settings: Settings) -> None:
        """Initialize Cloudflare Workers AI (native API)."""
        self.cf_api_key = getattr(settings, 'openai_api_key', '')
        self.cf_account_id = getattr(settings, 'cloudflare_account_id', '')
        self.cf_model = getattr(settings, 'openai_model', '@cf/llava-hf/llava-1.5-7b-hf')

        # Extract account ID from openai_api_base if not set directly
        if not self.cf_account_id:
            api_base = getattr(settings, 'openai_api_base', '')
            # Extract from URL like: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
            import re
            match = re.search(r'/accounts/([^/]+)/', api_base)
            if match:
                self.cf_account_id = match.group(1)

        if not self.cf_api_key:
            raise ValueError("OPENAI_API_KEY is required for Cloudflare provider")
        if not self.cf_account_id:
            raise ValueError("Could not determine Cloudflare account ID. Set OPENAI_API_BASE with account ID.")

        logger.info(f"Using Cloudflare native API with model: {self.cf_model}")

        # Accept license if needed (for Llama models)
        if 'llama' in self.cf_model.lower():
            self._accept_cloudflare_native_license()

    def _accept_cloudflare_native_license(self) -> None:
        """Accept Cloudflare Workers AI model license via native API."""
        try:
            api_url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run/{self.cf_model}"
            payload = {
                "prompt": "agree",
                "max_tokens": 10
            }

            response = httpx.post(
                api_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.cf_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                logger.info("Cloudflare model license accepted successfully")
            else:
                logger.debug(f"License acceptance response: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            logger.warning(f"Could not auto-accept Cloudflare license: {e}")

    def _init_openai(self, settings: Settings) -> None:
        """Initialize OpenAI-compatible API."""
        self.openai_api_key = getattr(settings, 'openai_api_key', '')
        self.openai_api_base = getattr(settings, 'openai_api_base', 'https://api.openai.com/v1')
        self.openai_model = getattr(settings, 'openai_model', 'gpt-4o')

        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using openai provider")

        logger.info(f"Using OpenAI provider: {self.openai_api_base} with model: {self.openai_model}")

        # Accept Cloudflare model license if needed (for Llama models)
        if 'cloudflare.com' in self.openai_api_base and 'llama' in self.openai_model.lower():
            self._accept_cloudflare_license()

    def _accept_cloudflare_license(self) -> None:
        """Accept Cloudflare Workers AI model license (required for Llama models)."""
        try:
            api_url = f"{self.openai_api_base.rstrip('/')}/chat/completions"
            payload = {
                "model": self.openai_model,
                "messages": [{"role": "user", "content": "agree"}],
                "max_tokens": 10
            }

            response = httpx.post(
                api_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                logger.info("Cloudflare model license accepted successfully")
            elif "already accepted" in response.text.lower() or response.status_code == 200:
                logger.info("Cloudflare model license was already accepted")
            else:
                logger.debug(f"License acceptance response: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            logger.warning(f"Could not auto-accept Cloudflare license: {e}")

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

            content_type = response.headers.get("content-type", "unknown")
            logger.debug(f"Image response: status={response.status_code}, type={content_type}, size={len(response.content)}")

            if "text/html" in content_type:
                logger.error(f"Got HTML instead of image - likely auth issue. First 200 chars: {response.text[:200]}")
                return None

            return Image.open(BytesIO(response.content))
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
            return None

    def _image_to_base64(self, img: Image.Image, format: str = "PNG") -> str:
        """Convert PIL Image to base64 string."""
        # Convert to RGB if needed (for JPEG compatibility)
        if format == "JPEG" and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buffer = BytesIO()
        img.save(buffer, format=format)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _get_captcha_page(self) -> tuple[str | None, list[str] | None]:
        """
        Fetch the captcha page and extract prompt and image hashes.

        Returns:
            Tuple of (prompt, image_hashes) or (None, None) if failed.
        """
        try:
            response = self._http_client.get(self.CAPTCHA_PAGE_URL)
            response.raise_for_status()
            html = response.text

            self._last_captcha_html = html

            logger.debug(f"Captcha page status: {response.status_code}, length: {len(html)}")

            if "already verified" in html.lower() or "do not need to verify" in html.lower():
                logger.info("Account is already verified - no captcha needed")
                return -1, "already_verified"

            if "login" in html.lower() or "sign in" in html.lower():
                logger.error("Captcha page appears to be a login redirect!")
                logger.debug(f"Page content (first 500 chars): {html[:500]}")
                return None, None

            hash_patterns = [
                re.compile(r"chooseItem\('(\$2y\$10\$[^']+)',\s*false\)"),
                re.compile(r'chooseItem\("(\$2y\$10\$[^"]+)",\s*false\)'),
                re.compile(r'data-hash="(\$2y\$10\$[^"]+)"'),
                re.compile(r"'(\$2y\$10\$[^']{50,})'"),
                re.compile(r'"(\$2y\$10\$[^"]{50,})"'),
            ]

            image_hashes = None
            for i, pattern in enumerate(hash_patterns):
                matches = pattern.findall(html)
                if len(matches) >= 4:
                    image_hashes = matches[:4]
                    logger.debug(f"Found {len(image_hashes)} image hashes using pattern {i+1}")
                    break

            if not image_hashes:
                logger.warning("Could not extract image hashes from page")
                if "chooseItem" in html:
                    logger.debug("Found 'chooseItem' in page, but pattern didn't match")
                    idx = html.find("chooseItem")
                    logger.debug(f"chooseItem context: {html[max(0,idx-20):idx+100]}")
                if "$2y$" in html:
                    logger.debug("Found '$2y$' hash pattern in page")
                else:
                    logger.debug("No '$2y$' hash found in page - might be different captcha type")
                logger.debug(f"Page content (first 2000 chars): {html[:2000]}")

            prompt = None
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

        prompt, image_hashes = self._get_captcha_page()
        self._image_hashes = image_hashes

        if not image_hashes:
            logger.error("Failed to extract image hashes from captcha page")
            return None, prompt

        if not prompt:
            prompt = "the item that is different from the others"

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

        if self.provider == 'cloudflare':
            answer = self._solve_with_cloudflare(images, prompt)
        elif self.provider == 'openai':
            answer = self._solve_with_openai(images, prompt)
        else:
            answer = self._solve_with_gemini(images, prompt)

        return answer, prompt

    def is_quota_exhausted(self) -> bool:
        """Check if API quota is currently exhausted."""
        return time.time() < self._quota_exhausted_until

    def get_quota_wait_time(self) -> int:
        """Get seconds until quota resets (0 if not exhausted)."""
        remaining = self._quota_exhausted_until - time.time()
        return max(0, int(remaining))

    def _solve_with_gemini(self, images: list[Image.Image], prompt: str) -> int | None:
        """Use Gemini to identify the correct image."""
        if self.is_quota_exhausted():
            wait_time = self.get_quota_wait_time()
            logger.warning(f"API quota exhausted. Waiting {wait_time}s until reset...")
            if wait_time > 300:
                logger.error(f"Quota exhausted for {wait_time}s - skipping captcha solve")
                return None
            time.sleep(wait_time)

        gemini_prompt = self.CAPTCHA_PROMPT.format(prompt=prompt)
        content = [gemini_prompt]

        for i, img in enumerate(images, 1):
            content.append(f"\n\nImage {i}:")
            content.append(img)

        max_retries = 3
        retry_delays = [30, 60, 120]

        for attempt in range(max_retries + 1):
            try:
                response = self.gemini_model.generate_content(content)
                answer = response.text.strip()

                logger.debug(f"Gemini raw response: {answer}")

                for char in answer:
                    if char in "1234":
                        result = int(char)
                        logger.info(f"Captcha solved: selected image {result}")
                        return result

                logger.warning(f"Could not parse Gemini response: {answer}")
                return None

            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or ("resource" in error_str and "exhausted" in error_str)
                is_quota_exhausted = "quota" in error_str or "exhausted" in error_str

                if is_quota_exhausted:
                    self._quota_exhausted_until = time.time() + 3600
                    logger.error(f"API daily quota exhausted. Will retry in 1 hour.")
                    logger.error("Consider switching to a different provider (CAPTCHA_PROVIDER=openai).")
                    return None

                if is_rate_limit and attempt < max_retries:
                    delay = retry_delays[attempt]
                    logger.warning(f"Rate limited, waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(delay)
                    continue

                logger.error(f"Error solving captcha with Gemini: {e}")
                return None

        return None

    def _create_grid_image(self, images: list[Image.Image]) -> Image.Image:
        """Create a 2x2 grid image from 4 images with numbered labels."""
        # Get max dimensions
        max_width = max(img.width for img in images)
        max_height = max(img.height for img in images)

        # Create grid image with padding for labels
        padding = 30
        grid_width = max_width * 2 + padding
        grid_height = max_height * 2 + padding

        grid = Image.new('RGB', (grid_width, grid_height), 'white')

        # Place images in grid: 1=top-left, 2=top-right, 3=bottom-left, 4=bottom-right
        positions = [
            (padding // 2, padding),  # Image 1: top-left
            (max_width + padding // 2, padding),  # Image 2: top-right
            (padding // 2, max_height + padding),  # Image 3: bottom-left
            (max_width + padding // 2, max_height + padding),  # Image 4: bottom-right
        ]

        for i, (img, pos) in enumerate(zip(images, positions), 1):
            # Resize image if needed
            if img.width != max_width or img.height != max_height:
                img = img.resize((max_width, max_height), Image.Resampling.LANCZOS)
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            grid.paste(img, pos)

        return grid

    def _solve_with_cloudflare(self, images: list[Image.Image], prompt: str) -> int | None:
        """Use Cloudflare Workers AI native API to identify the correct image."""
        if self.is_quota_exhausted():
            wait_time = self.get_quota_wait_time()
            logger.warning(f"API quota exhausted. Waiting {wait_time}s until reset...")
            if wait_time > 300:
                logger.error(f"Quota exhausted for {wait_time}s - skipping captcha solve")
                return None
            time.sleep(wait_time)

        # Create a grid image of all 4 images
        grid_image = self._create_grid_image(images)

        # Convert to JPEG bytes - Cloudflare expects raw bytes as list of integers
        if grid_image.mode in ("RGBA", "P"):
            grid_image = grid_image.convert("RGB")
        buffer = BytesIO()
        grid_image.save(buffer, format="JPEG", quality=85)
        img_bytes = list(buffer.getvalue())

        # Build prompt for grid-based selection
        cf_prompt = f"""This image shows a 2x2 grid of 4 images:
- Image 1 is in the TOP-LEFT
- Image 2 is in the TOP-RIGHT
- Image 3 is in the BOTTOM-LEFT
- Image 4 is in the BOTTOM-RIGHT

Task: Find the image that shows "{prompt}"

Look at each quadrant carefully and identify which ONE image matches "{prompt}".

Respond with ONLY a single digit: 1, 2, 3, or 4"""

        # Cloudflare native API endpoint
        api_url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run/{self.cf_model}"

        # Cloudflare Workers AI expects image as array of bytes (list of integers 0-255)
        payload = {
            "image": img_bytes,
            "prompt": cf_prompt,
            "max_tokens": 50
        }

        try:
            response = httpx.post(
                api_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.cf_api_key}",
                    "Content-Type": "application/json"
                },
                timeout=60.0
            )

            if response.status_code >= 400:
                logger.error(f"Cloudflare API error {response.status_code}: {response.text[:500]}")

            response.raise_for_status()
            result = response.json()
            logger.debug(f"Cloudflare API raw result: {result}")

            # Parse Cloudflare native response format
            if result.get("success") and "result" in result:
                cf_result = result["result"]
                if isinstance(cf_result, dict):
                    # Try different response field names (varies by model)
                    answer = cf_result.get("response") or cf_result.get("description") or ""
                    answer = answer.strip()
                elif isinstance(cf_result, str):
                    answer = cf_result.strip()
                else:
                    logger.error(f"Unknown Cloudflare result format: {cf_result}")
                    return None

                logger.debug(f"Cloudflare raw response: {answer}")

                # Extract number from response
                for char in answer:
                    if char in "1234":
                        result_num = int(char)
                        logger.info(f"Captcha solved: selected image {result_num}")
                        return result_num

                logger.warning(f"Could not parse Cloudflare response: {answer}")
                return None
            else:
                logger.error(f"Cloudflare API returned error: {result}")
                return None

        except Exception as e:
            logger.error(f"Error solving captcha with Cloudflare: {e}")
            return None

    def _solve_with_openai(self, images: list[Image.Image], prompt: str) -> int | None:
        """Use OpenAI-compatible API to identify the correct image."""
        if self.is_quota_exhausted():
            wait_time = self.get_quota_wait_time()
            logger.warning(f"API quota exhausted. Waiting {wait_time}s until reset...")
            if wait_time > 300:
                logger.error(f"Quota exhausted for {wait_time}s - skipping captcha solve")
                return None
            time.sleep(wait_time)

        # Build the message with images
        text_prompt = self.CAPTCHA_PROMPT.format(prompt=prompt)

        # Create content array with text and images
        content = [{"type": "text", "text": text_prompt}]

        for i, img in enumerate(images, 1):
            img_base64 = self._image_to_base64(img)
            content.append({"type": "text", "text": f"\n\nImage {i}:"})
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}"
                }
            })

        payload = {
            "model": self.openai_model,
            "messages": [
                {
                    "role": "user",
                    "content": content
                }
            ],
            "max_tokens": 10
        }

        max_retries = 3
        retry_delays = [30, 60, 120]

        for attempt in range(max_retries + 1):
            try:
                api_url = f"{self.openai_api_base.rstrip('/')}/chat/completions"

                response = httpx.post(
                    api_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    timeout=60.0
                )

                if response.status_code == 429:
                    if attempt < max_retries:
                        delay = retry_delays[attempt]
                        logger.warning(f"Rate limited, waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                        time.sleep(delay)
                        continue
                    else:
                        self._quota_exhausted_until = time.time() + 3600
                        logger.error("API quota exhausted. Will retry in 1 hour.")
                        return None

                # Log error details for non-2xx responses before raising
                if response.status_code >= 400:
                    logger.error(f"API error {response.status_code}: {response.text[:500]}")

                response.raise_for_status()
                result = response.json()
                logger.debug(f"OpenAI API raw result: {result}")

                # Handle different response formats (OpenAI vs Cloudflare AI)
                answer = None

                if "choices" in result and result["choices"]:
                    # Standard OpenAI format
                    message = result["choices"][0].get("message", {})
                    content = message.get("content")

                    if content:
                        answer = content.strip()
                    else:
                        # Model returned empty response - likely doesn't support vision in this format
                        logger.error(f"Model returned empty content. Message: {message}")
                        logger.error("This usually means the model doesn't support vision or the request format is wrong.")
                        logger.error("For Cloudflare AI, try models like: @cf/meta/llama-3.2-11b-vision-instruct")
                        return None

                elif "result" in result:
                    # Cloudflare AI native format (non-OpenAI compatible endpoint)
                    cf_result = result["result"]
                    if isinstance(cf_result, dict) and "response" in cf_result:
                        answer = cf_result["response"].strip()
                    elif isinstance(cf_result, str):
                        answer = cf_result.strip()
                    else:
                        logger.error(f"Unknown Cloudflare result format: {cf_result}")
                        return None
                else:
                    logger.error(f"Unknown API response format: {result}")
                    return None

                if not answer:
                    logger.error("API returned empty answer")
                    return None

                logger.debug(f"OpenAI raw response: {answer}")

                for char in answer:
                    if char in "1234":
                        result_num = int(char)
                        logger.info(f"Captcha solved: selected image {result_num}")
                        return result_num

                logger.warning(f"Could not parse OpenAI response: {answer}")
                return None

            except httpx.HTTPStatusError as e:
                error_str = str(e).lower()
                if "429" in error_str or "rate" in error_str:
                    if attempt < max_retries:
                        delay = retry_delays[attempt]
                        logger.warning(f"Rate limited, waiting {delay}s before retry...")
                        time.sleep(delay)
                        continue
                logger.error(f"Error solving captcha with OpenAI: {e}")
                return None
            except Exception as e:
                logger.error(f"Error solving captcha with OpenAI: {e}")
                return None

        return None

    def submit_captcha_answer(self, answer: int) -> bool:
        """Submit the captcha answer to the bot verification API."""
        try:
            answer_index = answer - 1

            if not hasattr(self, '_image_hashes') or not self._image_hashes:
                logger.error("No image hashes available for submission")
                return False

            if answer_index < 0 or answer_index >= len(self._image_hashes):
                logger.error(f"Invalid answer index: {answer_index}")
                return False

            selected_hash = self._image_hashes[answer_index]
            logger.debug(f"Selected hash for image {answer}: {selected_hash[:30]}...")

            import random
            payload = {
                "data": selected_hash,
                "x": random.randint(500, 700),
                "y": random.randint(300, 500),
                "valid": False,
            }

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

                    if result.get("success") or result.get("result") == "success":
                        logger.info("Captcha verified successfully!")
                        return True
                    elif result.get("error") or result.get("result") == "error":
                        logger.warning(f"Captcha verification failed: {result}")
                        return False
                    else:
                        logger.info("Captcha submission completed")
                        return True
                except Exception:
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

        if self.provider == 'openai':
            return self._solve_with_openai(images, "the item that is different")
        return self._solve_with_gemini(images, "the item that is different")

    def solve_from_images(self, images: list[Image.Image]) -> int | None:
        """Solve captcha from PIL Images."""
        if len(images) != 4:
            logger.error(f"Expected 4 images, got {len(images)}")
            return None
        if self.provider == 'openai':
            return self._solve_with_openai(images, "the item that is different")
        return self._solve_with_gemini(images, "the item that is different")
