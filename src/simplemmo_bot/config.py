"""Configuration module with environment variable support."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Bot configuration loaded from environment variables."""

    # API credentials
    simplemmo_api_token: str = Field(..., description="SimpleMMO API token from browser")
    simplemmo_session_cookie: str = Field(default="", description="SimpleMMO session cookie for web auth")
    gemini_api_key: str = Field(..., description="Google Gemini API key for captcha")

    # Bot behavior
    step_delay_min: int = Field(default=3, ge=1, description="Minimum delay between steps (seconds)")
    step_delay_max: int = Field(default=8, ge=1, description="Maximum delay between steps (seconds)")
    steps_per_session: int = Field(default=100, ge=1, description="Steps before pause")

    # Features
    auto_fight_npc: bool = Field(default=True, description="Automatically fight NPCs")
    auto_gather_materials: bool = Field(default=True, description="Automatically gather materials")

    # API endpoints
    api_base_url: str = Field(default="https://api.simple-mmo.com")
    web_base_url: str = Field(default="https://web.simple-mmo.com")

    # Travel endpoint path (may change)
    travel_endpoint: str = Field(default="/api/travel/perform/kj8gzj4hd")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def get_settings() -> Settings:
    """Load and return settings from environment."""
    return Settings()
