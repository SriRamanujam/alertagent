from typing import Optional

import discord


class Discord:
    bot: Optional[discord.Client] = None

    @classmethod
    def get_client(cls) -> discord.Client:
        if cls.bot is None:
            cls.bot = discord.Client(intents=discord.Intents.default())
        return cls.bot
