import asyncio
import logging

import discord
from fastapi import FastAPI, Depends, HTTPException
from typing import Annotated

from .alertmanager import AlertmanagerAlert, AlertmanagerMessage
from .cache import cache
from .config import Settings, get_settings
from .integrations import on_startup, Discord, on_shutdown, Model
from .triage import triage_alert, build_alert_embed

from . import ALERT_STATUS_FIRING, ALERT_STATUS_RESOLVED

logger = logging.getLogger(__name__)

# Cap on concurrent LLM triage sessions so that many alerts coming at once
# don't overload the inference endpoint. Extra triages queue on the semaphore instead.
_triage_semaphore = asyncio.Semaphore(3)

app = FastAPI(on_startup=[on_startup], on_shutdown=[on_shutdown])


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}


async def _run_triage(chan, alert, model, timeout, scratch_dir) -> None:
    async with _triage_semaphore:
        await triage_alert(chan, alert, model, timeout, scratch_dir)


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

    model = Model.get()

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
            _run_triage(
                chan, alert, model, settings.response_timeout, settings.scratch_dir
            )
        )
        task.add_done_callback(lambda t, a=alert: _log_triage_failure(t, a))

    for alert in resolved:
        cache.forget(alert)
        await chan.send(embed=build_alert_embed(alert))
