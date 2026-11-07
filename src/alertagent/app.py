import asyncio
import logging

import discord
from fastapi import FastAPI, Depends, HTTPException
from typing import Annotated
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI

from .alertmanager import AlertmanagerAlert, AlertmanagerMessage
from .cache import cache
from .config import Settings, get_settings
from .session import on_startup, Discord, on_shutdown
from .triage import triage_alert, build_alert_embed

from . import ALERT_STATUS_FIRING, ALERT_STATUS_RESOLVED

logger = logging.getLogger(__name__)

app = FastAPI(on_startup=[on_startup], on_shutdown=[on_shutdown])


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}


def _log_triage_failure(task: asyncio.Task, alert: AlertmanagerAlert) -> None:
    # Likely we're shutting down, no need to log anything here
    if task.cancelled():
        return

    exc = task.exception()
    if exc is not None:
        # forget the alert so that repeats will run triage.
        cache.forget(alert)
        logger.exception(
            "triage failed for alert %s",
            alert.labels.get("alertname", "unknown alert"),
            exc_info=exc,
        )


@app.post("/", status_code=204)
async def alertmanager_ingest(
    message: AlertmanagerMessage, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    bot = Discord.get_client()
    chan = await bot.fetch_channel(settings.discord_channel_id)
    if not isinstance(chan, (discord.TextChannel, discord.Thread)):
        raise HTTPException(
            status_code=500, detail="configured Discord channel is not a text channel"
        )

    client = AsyncOpenAI(
        base_url=settings.openai_base_url, api_key=settings.openai_api_key
    )
    provider = OpenAIProvider(openai_client=client)
    model = OpenAIChatModel(
        model_name=settings.model_name,
        provider=provider,
        profile={"context_window": settings.context_size},
    )

    firing = [
        a
        for a in message.alerts
        if a.status == ALERT_STATUS_FIRING
        and a.labels.get("alertname", "") != "Watchdog"
    ]

    resolved = [a for a in message.alerts if a.status == ALERT_STATUS_RESOLVED]

    for alert in firing:
        if cache.seen(alert):
            logger.info(
                f"skipping duplicate alert {alert.labels.get("alertname", "unknown alert")} (startedAt {alert.starts_at})",
            )
            continue
        task = asyncio.create_task(
            triage_alert(
                chan, alert, model, settings.response_timeout, settings.scratch_dir
            )
        )
        task.add_done_callback(lambda t, a=alert: _log_triage_failure(t, a))

    for alert in resolved:
        cache.forget(alert)
        await chan.send(embed=build_alert_embed(alert))
