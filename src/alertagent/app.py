import asyncio

import humanize
from datetime import datetime, timezone

import discord
from fastapi import FastAPI, Depends, HTTPException
from typing import Annotated

from .alertmanager import AlertmanagerAlert, AlertmanagerMessage
from .config import Settings, get_settings
from .session import on_startup, Discord

app = FastAPI(on_startup=[on_startup])

ALERT_STATUS_FIRING = "firing"
ALERT_STATUS_RESOLVED = "resolved"

COLOR_GRAY = 9807270
COLOR_RED = 15145498
COLOR_YELLOW = 15646767
COLOR_BLUE = 7782616
COLOR_GREEN = 5763719

SEVERITY_DESCRIPTIONS = {
    "critical": "You should take a look at these, like, right now.",
    "warning": "These are probably issues.",
    "info": "These are not bad, but maybe you should take a look?",
}
SEVERITY_COLORS = {
    "critical": COLOR_RED,
    "warning": COLOR_YELLOW,
    "info": COLOR_BLUE,
}


def _humanized_delta(starts_at: datetime) -> str:
    t = starts_at.astimezone(timezone.utc)
    delta = datetime.now(timezone.utc) - t
    humanized = humanize.naturaltime(delta)
    return f"{t:%Y-%m-%d %H:%M} UTC ({humanized})"


def build_alert_embed(alert: AlertmanagerAlert) -> discord.Embed:
    """Render one alert as a Discord embed"""
    severity = alert.labels.get("severity", "none")
    embed = discord.Embed(
        title=severity.upper(),
        description=SEVERITY_DESCRIPTIONS.get(severity, "Unknown severity. Take a look at these"),
        color=COLOR_GREEN if alert.status == ALERT_STATUS_RESOLVED else SEVERITY_COLORS.get(severity, COLOR_GRAY),
    )
    embed.set_author(name=alert.labels.get("prometheus", ""), url=alert.generator_url.replace("///", "//"))
    embed.set_footer(text=_humanized_delta(alert.starts_at))
    body = alert.annotations.get("description")
    if body is None:
        body = alert.annotations.get("message", "")
    name = alert.labels.get("alertname", "No-name alert")

    # Discord hard-caps field name/value; over these the API rejects the whole message
    embed.add_field(name=name[:256], value=body[:1024])
    return embed


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Hello from alertagent!"}

@app.post("/", status_code=204)
async def alertmanager_ingest(
    message: AlertmanagerMessage,
    settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    bot = Discord.get_client()
    chan = await bot.fetch_channel(settings.discord_channel_id)
    if not isinstance(chan, (discord.TextChannel, discord.Thread)):
        raise HTTPException(status_code=500, detail="configured Discord channel is not a text channel")

    async def open_thread(alert: AlertmanagerAlert) -> discord.Thread:
        msg = await chan.send(embed=build_alert_embed(alert))
        return await msg.create_thread(name="Bot analysis", reason="Automated triage")

    firing = [
        a for a in message.alerts
        if a.status == ALERT_STATUS_FIRING and a.labels.get("alertname", "") != "Watchdog"
    ]
    threads = list(await asyncio.gather(*[open_thread(a) for a in firing]))

    # TODO: kick off the agent and pass it the message Id.
    # If we need to instantiate the thread first, then do that.
    print(", ".join([str(thread.id) for thread in threads]))