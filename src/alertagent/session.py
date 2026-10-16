from typing import Optional

import discord

from .config import get_settings


class Discord:
    bot: Optional[discord.Client] = None

    @classmethod
    def get_client(cls) -> discord.Client:
        if cls.bot is None:
            cls.bot = discord.Client(intents=discord.Intents.default())
        return cls.bot


async def on_startup() -> None:
    await Discord.get_client().login(get_settings().discord_bot_token)
