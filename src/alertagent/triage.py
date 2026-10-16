TRIAGE_INSTRUCTIONS = """
You are an experienced and compenent SRE. You are examining an OKD (based on Openshift) cluster.
You will be given an alert that is currently firing within the cluster. Provide your best explanation
for what the root cause is for the alert. Output only the root cause analysis text.

Structure your analysis as Markdown, with the following headings in this order:

# Root cause
# Evidence
# Suggested next steps
"""

from .alertmanager import AlertmanagerAlert
from . import (
    SEVERITY_COLORS,
    ALERT_STATUS_RESOLVED,
    SEVERITY_DESCRIPTIONS,
    COLOR_GREEN,
    COLOR_GRAY,
    DISCORD_MAX_MESSAGE_LENGTH,
)

from pydantic_ai import Agent
from pydantic_ai.models import Model
import discord
import humanize
import io
from datetime import datetime, timezone


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
        description=SEVERITY_DESCRIPTIONS.get(
            severity, "Unknown severity. Take a look at these"
        ),
        color=(
            COLOR_GREEN
            if alert.status == ALERT_STATUS_RESOLVED
            else SEVERITY_COLORS.get(severity, COLOR_GRAY)
        ),
    )
    embed.set_author(
        name=alert.labels.get("prometheus", ""),
        url=alert.generator_url.replace("///", "//"),
    )
    embed.set_footer(text=_humanized_delta(alert.starts_at))
    body = alert.annotations.get("description")
    if body is None:
        body = alert.annotations.get("message", "")
    name = alert.labels.get("alertname", "No-name alert")

    # Discord hard-caps field name/value; over these the API rejects the whole message
    embed.add_field(name=name[:256], value=body[:1024])
    return embed


def build_prompt(alert: AlertmanagerAlert) -> str:
    """Render an alert as a plain-text prompt for the agent. Intended to be user message for LLM"""

    # This works alarmingly well.
    return "JSON payload of alert message: \n" + alert.model_dump_json()


def build_triage_agent(model: Model) -> Agent:
    """Get a new agent instance"""
    return Agent(
        model=model, name="Triage", instructions=TRIAGE_INSTRUCTIONS, output_type=str
    )


async def triage_alert(
    channel: discord.TextChannel | discord.Thread,
    alert: AlertmanagerAlert,
    model: Model,
) -> None:
    """
    Most of the heavy lifting of the Discord loop happens here
    We send the initial alert to the channel, create the thread, dispatch the analysis to the LLM,
    wait on its response while sending a typing status to Discord, and then send the LLM's response.
    """
    msg = await channel.send(embed=build_alert_embed(alert))
    thread = await msg.create_thread(name="Bot analysis", reason="Automated triage")
    async with thread.typing():
        agent = build_triage_agent(model)
        result = await agent.run(build_prompt(alert))
        if len(result.output) < DISCORD_MAX_MESSAGE_LENGTH:
            await thread.send(result.output)
        else:
            file = discord.File(
                io.BytesIO(result.output.encode("utf-8")), filename="analysis.md"
            )
            try:
                await thread.send(file=file)
            except Exception as e:
                print(f"exception when uploading file to Discord: {e}")
