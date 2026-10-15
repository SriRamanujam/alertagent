import asyncio

from fastapi import FastAPI, Depends
from typing import Annotated

from .alertmanager import AlertmanagerMessage
from .config import Settings, get_settings
from .session import on_shutdown, on_startup, SingletonAioHttp

from discord import Webhook

app = FastAPI(on_shutdown=[on_shutdown], on_startup=[on_startup])

@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}

@app.post("/")
async def alertmanager_ingest(
    message: AlertmanagerMessage,
    settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, str | list[str]]:

    # instantiate the discord webhook object (TODO: maybe this doesn't need to happen every request)
    webhook = Webhook.from_url(settings.discord_webhook_url, session=SingletonAioHttp.get_client())

    messages = [
        alert.labels.get("alertname", "") + " " + alert.status
        for alert in message.alerts
    ]

    webhook_messages = await asyncio.gather(
        *(webhook.send(msg, wait=True) for msg in messages)
    )

    # TODO: kick off the agent and pass it the message Id.
    # If we need to instantiate the thread first, then do that.

    return {
        "discord_message_ids": [str(wm.id) for wm in webhook_messages],
        "alerts": ", ".join(messages),
    }