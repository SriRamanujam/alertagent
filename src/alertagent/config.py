from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_bot_token: str
    discord_channel_id: int

    openai_base_url: str
    model_name: str
    openai_api_key: str

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ALERTAGENT_")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore
