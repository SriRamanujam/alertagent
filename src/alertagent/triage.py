TRIAGE_INSTRUCTIONS = """
You are an experienced and compenent SRE. You are examining an OKD (based on Openshift) cluster.
You will be given an alert that is currently firing within the cluster. Use the tools provided
to investigate the cluster for details. Provide your best explanation
for what the root cause is for the alert. Output only the root cause analysis text. 
For large tool results, use read_tool_result with a pattern matching the field you need (e.g. \"reason\", \"failed\") and a small limit.

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
from .tools.kubernetes import kubernetes_tools
from .tools.websearch import websearch_tools

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai_harness import (
    CodeMode,
    TieredCompaction,
    ClearToolResults,
    SummarizingCompaction,
    ToolOutputLimits,
)
from pydantic_ai_harness.tool_output_limits import (
    Band,
    LocalFileStore,
    json_lines,
    Spill,
    Truncate,
)
import discord
import humanize
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


def build_triage_agent(model: Model, timeout: int, scratch_dir: Path) -> Agent:
    """Get a new agent instance"""
    return Agent(
        model=model,
        name="Triage",
        instructions=TRIAGE_INSTRUCTIONS,
        output_type=str,
        toolsets=[kubernetes_tools, websearch_tools],
        # TODO: not sure if this should be set _higher_ than the server-side timeout
        # or the exact same as the server-side timeout.
        model_settings=ModelSettings(timeout=timeout),
        capabilities=[
            CodeMode(tools={"code_mode": True}),
            # A single oversized tool return (pod logs, a big listing, code_mode
            # stdout) survives every compaction tier, since ClearToolResults keeps
            # the last 3 pairs. Spill the full payload to disk so the model can
            # page it back with read_tool_result. Never point base_dir at the
            # scratch root itself: LocalFileStore must own the dir and chmods it 0700.
            ToolOutputLimits(
                # Spill payloads as JSON Lines / indented JSON so read_tool_result's
                # line-oriented pattern filter returns matching lines, not the whole blob.
                serializer=json_lines,
                bands=[
                    Band(over=48_000, action=Spill(then=Truncate(max_chars=48_000)))
                ],
                store=LocalFileStore(
                    base_dir=scratch_dir / "alertagent-spills",
                    cleanup_after=timedelta(hours=1),
                ),
            ),
            TieredCompaction(
                tiers=[
                    ClearToolResults(keep_pairs=3, max_fraction=0.95),
                    SummarizingCompaction(max_fraction=0.95, keep_messages=5),
                ],
                target_fraction=0.45,
            ),
        ],
    )


async def triage_alert(
    channel: discord.TextChannel | discord.Thread,
    alert: AlertmanagerAlert,
    model: Model,
    timeout: int,
    scratch_dir: Path,
) -> None:
    """
    Most of the heavy lifting of the Discord loop happens here
    We send the initial alert to the channel, create the thread, dispatch the analysis to the LLM,
    wait on its response while sending a typing status to Discord, and then send the LLM's response.
    """
    msg = await channel.send(embed=build_alert_embed(alert))
    thread = await msg.create_thread(name="Bot analysis", reason="Automated triage")
    async with thread.typing():
        agent = build_triage_agent(model, timeout, scratch_dir)
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
