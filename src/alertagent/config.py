from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_bot_token: str
    discord_channel_id: int

    openai_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-5-mini"
    openai_api_key: str

    min_triage_severity: Literal["info", "warning", "critical"] = "warning"
    """Lowest severity the agent triages. Lower-severity alerts are posted to the channel without triage."""

    response_timeout: int = 300
    """Timeout in seconds before the agent retries a request."""

    context_size: int = 131072

    scratch_dir: Path = Path("/tmp")
    """Root for spilled tool output; a dedicated 0700 subdir is created under it."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ALERTAGENT_")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore
