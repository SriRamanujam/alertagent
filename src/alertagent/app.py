import asyncio

import discord
from fastapi import FastAPI, Depends, HTTPException
from typing import Annotated

from .alertmanager import AlertmanagerMessage
from .config import Settings, get_settings
from .session import on_startup, Discord

app = FastAPI(on_startup=[on_startup])

ALERT_STATUS_FIRING = "firing"
ALERT_STATUS_RESOLVED = "resolved"

@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}

@app.post("/")
async def alertmanager_ingest(
    message: AlertmanagerMessage,
    settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, str | list[str]]:
    bot = Discord.get_client()
    chan = await bot.fetch_channel(settings.discord_channel_id)
    if not isinstance(chan, (discord.TextChannel, discord.Thread)):
        raise HTTPException(status_code=500, detail="configured Discord channel is not a text channel")

    messages: list[int] = []
    for alert in message.alerts:
        if alert.labels.get("alertname", "") == "Watchdog":
            continue

        if alert.status != ALERT_STATUS_FIRING:
            continue

        msg = await chan.send('hello' + alert.labels.get("alertname", "UNKNOWN"))
        messages.append(msg.id)



    # TODO: kick off the agent and pass it the message Id.
    # If we need to instantiate the thread first, then do that.

    return {
        "discord_message_ids": [str(wm) for wm in messages],
    }