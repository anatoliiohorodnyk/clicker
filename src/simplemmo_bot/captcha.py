"""Captcha solver using Google Gemini Vision API."""

import logging
import base64
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image
import google.generativeai as genai

from .config import Settings

logger = logging.getLogger(__name__)


class CaptchaSolver:
    """Solves SimpleMMO captcha using Google Gemini Vision."""

    # Prompt for Gemini to identify the correct image
    CAPTCHA_PROMPT = """You are solving a SimpleMMO captcha.

You will see 4 images. One of them is DIFFERENT from the others.
Your task is to identify which image is the ODD ONE OUT.

Look for differences in:
- Object type (different item, animal, character)
- Color scheme
- Shape
- Category

Respond with ONLY the number (1, 2, 3, or 4) of the image that is different.
Do not explain, just respond with a single digit."""

    def __init__(self, settings: Settings) -> None:
        """Initialize captcha solver with Gemini API."""
        self.settings = settings
        genai.configure(api_key=settings.gemini_api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
        self._http_client = httpx.Client(timeout=30.0)

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
            return Image.open(BytesIO(response.content))
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
            return None

    def solve_from_urls(self, image_urls: list[str]) -> int | None:
        """
        Solve captcha from image URLs.

        Args:
            image_urls: List of 4 image URLs.

        Returns:
            Index (1-4) of the odd image, or None if failed.
        """
        if len(image_urls) != 4:
            logger.error(f"Expected 4 images, got {len(image_urls)}")
            return None

        images = []
        for url in image_urls:
            img = self.download_image(url)
            if img is None:
                return None
            images.append(img)

        return self.solve_from_images(images)

    def solve_from_images(self, images: list[Image.Image]) -> int | None:
        """
        Solve captcha from PIL Images.

        Args:
            images: List of 4 PIL Image objects.

        Returns:
            Index (1-4) of the odd image, or None if failed.
        """
        if len(images) != 4:
            logger.error(f"Expected 4 images, got {len(images)}")
            return None

        try:
            # Prepare content for Gemini
            content = [self.CAPTCHA_PROMPT]

            for i, img in enumerate(images, 1):
                content.append(f"\nImage {i}:")
                content.append(img)

            # Send to Gemini
            response = self.model.generate_content(content)
            answer = response.text.strip()

            logger.debug(f"Gemini response: {answer}")

            # Parse response - expect single digit 1-4
            for char in answer:
                if char in "1234":
                    result = int(char)
                    logger.info(f"Captcha solved: image {result} is the odd one")
                    return result

            logger.warning(f"Could not parse Gemini response: {answer}")
            return None

        except Exception as e:
            logger.error(f"Error solving captcha with Gemini: {e}")
            return None

    def solve_from_files(self, file_paths: list[str | Path]) -> int | None:
        """
        Solve captcha from local image files.

        Args:
            file_paths: List of 4 file paths.

        Returns:
            Index (1-4) of the odd image, or None if failed.
        """
        images = []
        for path in file_paths:
            try:
                img = Image.open(path)
                images.append(img)
            except Exception as e:
                logger.error(f"Failed to open image {path}: {e}")
                return None

        return self.solve_from_images(images)
