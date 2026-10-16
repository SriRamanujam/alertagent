from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_bot_token: str
    discord_channel_id: int

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ALERTAGENT_")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore
