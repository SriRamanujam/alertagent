from .discord import Discord
from .model import Model

from ..config import get_settings


async def on_startup() -> None:
    await Discord.get_client().login(get_settings().discord_bot_token)


async def on_shutdown() -> None:
    await Discord.get_client().close()
    await Model.on_shutdown()
