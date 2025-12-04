"""Authentication module - handles automatic login to SimpleMMO."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class SessionCredentials:
    """Session credentials obtained from login."""

    laravel_session: str
    xsrf_token: str
    api_token: str = ""


class SimpleMMOAuth:
    """Handles authentication with SimpleMMO web interface."""

    LOGIN_PAGE_URL = "https://web.simple-mmo.com/login/credentials"
    LOGIN_URL = "https://web.simple-mmo.com/login"

    DEFAULT_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    def __init__(self, settings: Settings) -> None:
        """Initialize auth handler with settings."""
        self.settings = settings
        self._client = httpx.Client(
            headers=self.DEFAULT_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def login(self) -> SessionCredentials | None:
        """
        Perform login and return session credentials.

        Returns:
            SessionCredentials if successful, None otherwise.
        """
        if not self.settings.simplemmo_email or not self.settings.simplemmo_password:
            logger.error("Email and password required for auto-login")
            return None

        try:
            # Step 1: Get the login page to obtain CSRF token and initial cookies
            logger.info("Fetching login page...")
            login_page_response = self._client.get(self.LOGIN_PAGE_URL)
            login_page_response.raise_for_status()

            # Extract CSRF token from HTML
            csrf_token = self._extract_csrf_token(login_page_response.text)
            if not csrf_token:
                logger.error("Could not find CSRF token in login page")
                return None

            logger.debug(f"Found CSRF token: {csrf_token[:20]}...")

            # Get cookies from initial request
            initial_cookies = dict(login_page_response.cookies)
            logger.debug(f"Initial cookies: {list(initial_cookies.keys())}")

            # Step 2: Perform login
            logger.info(f"Logging in as {self.settings.simplemmo_email}...")

            login_data = {
                "_token": csrf_token,
                "email": self.settings.simplemmo_email,
                "password": self.settings.simplemmo_password,
                "remember": "on",
            }

            login_response = self._client.post(
                self.LOGIN_URL,
                data=login_data,
                headers={
                    **self.DEFAULT_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://web.simple-mmo.com",
                    "Referer": self.LOGIN_PAGE_URL,
                },
                cookies=initial_cookies,
            )

            # Check if login was successful
            # Successful login redirects to home/travel, failed stays on login page
            if "login" in str(login_response.url) and "credentials" in str(login_response.url):
                logger.error("Login failed - check email and password")
                return None

            # Extract session cookies from response
            all_cookies = dict(self._client.cookies)

            laravel_session = all_cookies.get("laravelsession", "")
            xsrf_token = all_cookies.get("XSRF-TOKEN", "")

            # URL decode the tokens if needed
            if xsrf_token:
                xsrf_token = unquote(xsrf_token)

            if not laravel_session or not xsrf_token:
                logger.error("Login succeeded but could not extract session cookies")
                logger.debug(f"Available cookies: {list(all_cookies.keys())}")
                return None

            logger.info("Login successful!")
            logger.debug(f"Laravel session length: {len(laravel_session)}")
            logger.debug(f"XSRF token length: {len(xsrf_token)}")

            # Step 3: Extract API token from the response page
            api_token = self._extract_api_token(login_response.text)
            if not api_token:
                # Try fetching home page to get API token
                logger.debug("API token not in login response, fetching home page...")
                home_response = self._client.get("https://web.simple-mmo.com/home")
                api_token = self._extract_api_token(home_response.text)

            if api_token:
                logger.info(f"API token obtained (length: {len(api_token)})")
            else:
                logger.warning("Could not extract API token - will need manual configuration")

            return SessionCredentials(
                laravel_session=laravel_session,
                xsrf_token=xsrf_token,
                api_token=api_token or "",
            )

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error during login: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error during login: {e}")
            return None

    def _extract_csrf_token(self, html: str) -> str | None:
        """Extract CSRF token from login page HTML."""
        # Look for: <input type="hidden" name="_token" value="...">
        token_pattern = re.compile(
            r'<input[^>]*name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        match = token_pattern.search(html)
        if match:
            return match.group(1)

        # Alternative pattern: value before name
        token_pattern_alt = re.compile(
            r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']_token["\']',
            re.IGNORECASE,
        )
        match = token_pattern_alt.search(html)
        if match:
            return match.group(1)

        # Try meta tag: <meta name="csrf-token" content="...">
        meta_pattern = re.compile(
            r'<meta[^>]*name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        match = meta_pattern.search(html)
        if match:
            return match.group(1)

        return None

    def _extract_api_token(self, html: str) -> str | None:
        """Extract API token from page HTML.

        Looks for: <meta name="api-token" content="...">
        """
        # Pattern: <meta name="api-token" content="...">
        api_token_pattern = re.compile(
            r'<meta[^>]*name=["\']api-token["\'][^>]*content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        match = api_token_pattern.search(html)
        if match:
            return match.group(1)

        # Alternative: content before name
        api_token_pattern_alt = re.compile(
            r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']api-token["\']',
            re.IGNORECASE,
        )
        match = api_token_pattern_alt.search(html)
        if match:
            return match.group(1)

        return None


def auto_login(settings: Settings) -> SessionCredentials | None:
    """
    Convenience function to perform auto-login.

    Args:
        settings: Bot settings with email and password.

    Returns:
        SessionCredentials if successful, None otherwise.
    """
    auth = SimpleMMOAuth(settings)
    try:
        return auth.login()
    finally:
        auth.close()
