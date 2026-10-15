from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    discord_webhook_url: str

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ALERTAGENT_")

@lru_cache
def get_settings() -> Settings:
    # required field is filled from the environment at runtime
    return Settings()  # type: ignore
