TRIAGE_INSTRUCTIONS = """
You are an experienced and competent SRE. You are examining an OKD (based on Openshift) cluster.
You will be given an alert that is currently firing within the cluster. Use the tools provided
to investigate the cluster for details. Provide your best explanation
for what the root cause is for the alert. Output only the root cause analysis text. 
For large tool results, use read_tool_result with a pattern matching the field you need (e.g. \"reason\", \"failed\") and a small limit.
Pod logs are plain text, not JSON: read them as text, do not run them through json.loads.

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

from pydantic_ai import Agent, UsageLimits
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
from pydantic_ai.exceptions import (
    UsageLimitExceeded,
    ModelHTTPError,
    ModelAPIError,
    UnexpectedModelBehavior,
    IncompleteToolCall,
    ContentFilterError,
    RunCancelled,
    UserError,
)
from asyncio import CancelledError, sleep
import discord
import humanize
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _chunk(text: str, limit: int) -> list[str]:
    """Split text into chunks of at most `limit` chars, breaking on line
    boundaries where possible so a Markdown heading isn't split mid-line.
    A single line longer than the limit is hard-split."""
    # Phase 1: break every line into pieces that fit within the limit.
    pieces: list[str] = []
    for line in text.splitlines(keepends=True):
        pieces.extend(line[i : i + limit] for i in range(0, len(line), limit))
    # Phase 2: greedily pack whole pieces into chunks of at most `limit` chars.
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > limit:
            chunks.append(current)
            current = ""
        current += piece
    if current:
        chunks.append(current)
    return chunks


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


def _recovery_for(exc: Exception) -> tuple[str, bool]:
    """Map a failed agent run to (message to post to the thread, retry once).

    Retry is only for failures a fresh run can plausibly fix: transient
    transport/endpoint problems and unusable model output (fresh sampling).
    Misconfiguration and loop-burned runs fail identically on retry, so they
    abort with an actionable message instead.
    """
    if isinstance(exc, ModelHTTPError):
        if exc.status_code in (401, 403):
            return (
                f"Bot misconfigured: the model rejected its API key (HTTP {exc.status_code}). Check the model credentials.",
                False,
            )
        if exc.status_code == 404:
            suggestion = (
                f" Did you mean {exc.suggested_model_id!r}?"
                if exc.suggested_model_id
                else ""
            )
            return (
                f"Bot misconfigured: model {exc.model_name!r} not found (HTTP 404).{suggestion}",
                False,
            )
        if exc.status_code < 500 and exc.status_code not in (408, 429):
            return (
                f"Bot misconfigured: the model rejected the request (HTTP {exc.status_code}): {str(exc)[:300]}",
                False,
            )
        return f"inference endpoint unavailable (HTTP {exc.status_code})", True
    if isinstance(exc, ModelAPIError):
        return (
            "could not reach the inference endpoint (timeout or connection failure)",
            True,
        )
    if isinstance(exc, IncompleteToolCall):
        return (
            "Bot misconfigured: the model's output was cut off mid-tool-call. Raise the model's max_tokens.",
            False,
        )
    if isinstance(exc, ContentFilterError):
        return "Analysis blocked by the provider's safety filter.", False
    if isinstance(exc, UnexpectedModelBehavior):
        message = str(exc)
        if "exceeded max retries count" in message:
            return (
                "Analysis aborted: a tool kept failing and its retry budget was exhausted. Check the failing tool (cluster API, websearch).",
                False,
            )
        if "exceeded before any response was generated" in message:
            return (
                "Bot misconfigured: the model's token limit is too small for triage. Raise the model's max_tokens.",
                False,
            )
        return "the model returned an unusable response", True
    if isinstance(exc, UsageLimitExceeded):
        return (
            "Analysis aborted after 200 model requests \u2014 the model appears stuck in a tool loop. "
            "Check cluster API reachability and websearch health.",
            False,
        )
    if isinstance(exc, RunCancelled):
        return "Analysis cancelled.", False
    if isinstance(exc, UserError):
        return f"Bot misconfigured (developer error): {str(exc)[:300]}", False
    if isinstance(exc, OSError):
        return "a tool's file I/O failed (spilled file missing or disk error)", True
    return f"{type(exc).__name__}: {str(exc)[:300]}", False


def _backoff(exc: Exception) -> float:
    """Seconds to wait before retrying a failed run: the provider's
    Retry-After when present, else a fixed 5s. Capped at 30s so a quota-reset
    header can't hold a triage semaphore slot for minutes."""
    if isinstance(exc, ModelHTTPError) and exc.headers:
        try:
            return min(max(float(exc.headers["retry-after"]), 0.0), 30.0)
        except KeyError, ValueError:
            pass
    return 5.0


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
        usage_limits = UsageLimits(request_limit=200)
        prompt = build_prompt(alert)

        try:
            result = await agent.run(prompt, usage_limits=usage_limits)
        except CancelledError:
            raise
        except (
            ModelHTTPError,
            ModelAPIError,
            UnexpectedModelBehavior,
            IncompleteToolCall,
            ContentFilterError,
            UsageLimitExceeded,
            RunCancelled,
            UserError,
            OSError,
        ) as exc:
            message, retry = _recovery_for(exc)
            if retry:
                await sleep(_backoff(exc))  # NB: this is asyncio's sleep
                try:
                    result = await agent.run(prompt, usage_limits=usage_limits)
                except Exception as second:
                    message, _ = _recovery_for(second)
                    await thread.send(f"Analysis failed: {message}")
                    raise
            else:
                await thread.send(message)
                raise
        except Exception:
            await thread.send("Analysis failed with an unexpected error.")
            raise

        if len(result.output) < DISCORD_MAX_MESSAGE_LENGTH:
            await thread.send(result.output)
        else:
            file = discord.File(
                io.BytesIO(result.output.encode("utf-8")), filename="analysis.md"
            )
            try:
                await thread.send(file=file)
            except (discord.DiscordException, OSError) as e:
                logger.warning(
                    "analysis file upload failed (%s: %s); sending analysis as text",
                    type(e).__name__,
                    e,
                    exc_info=e,
                )
                await thread.send(
                    "Couldn't upload the analysis as a file; the full "
                    "analysis follows as text below."
                )
                for chunk in _chunk(result.output, DISCORD_MAX_MESSAGE_LENGTH):
                    await thread.send(chunk)
