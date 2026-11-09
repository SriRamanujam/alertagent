import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

import discord
import pytest
from pydantic_ai.capabilities.abstract import leaf_capabilities
from pydantic_ai.exceptions import (
    ContentFilterError,
    IncompleteToolCall,
    ModelAPIError,
    ModelHTTPError,
    RunCancelled,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
    UserError,
)
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import ToolOutputLimits
from pydantic_ai_harness.tool_output_limits import Band, LocalFileStore, Spill, Truncate

from alertagent.alertmanager import AlertmanagerAlert
from alertagent.tools.websearch import websearch_tools
import alertagent.triage as triage_module
from alertagent.triage import (
    TRIAGE_INSTRUCTIONS,
    _backoff,
    _chunk,
    _recovery_for,
    build_prompt,
    build_triage_agent,
)

# same path FastAPI takes on the webhook body, so the fixture matches production input
ALERT = AlertmanagerAlert.model_validate(
    {
        "status": "firing",
        "labels": {
            "alertname": "KubePodCrashLooping",
            "severity": "warning",
            "prometheus": "kube-state-metrics",
        },
        "annotations": {"description": "Pods in crashloop backoff"},
        "startsAt": "2026-10-15T12:00:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://localhost:9093/graph",
    }
)


def test_build_prompt_contains_alert_fields() -> None:
    prompt = build_prompt(ALERT)
    assert "KubePodCrashLooping" in prompt  # alertname
    assert "warning" in prompt  # severity label
    assert "Pods in crashloop backoff" in prompt  # annotation
    assert "2026-10-15T12:00:00Z" in prompt  # startsAt


ANALYSIS = "# Root cause\nFake crashloop analysis."
REQUEST = {}  # captured from the fake model's request


def fake_model(messages, agent_info):
    user_parts = [p for p in messages[-1].parts if isinstance(p, UserPromptPart)]
    REQUEST["user_prompt"] = user_parts[0].content if user_parts else None
    REQUEST["instructions"] = agent_info.instructions
    return ModelResponse([TextPart(ANALYSIS)])


def test_agent_run_with_function_model(tmp_path) -> None:
    agent = build_triage_agent(
        FunctionModel(fake_model), timeout=300, scratch_dir=tmp_path
    )
    result = asyncio.run(agent.run(build_prompt(ALERT)))
    # the prompt built from the alert is exactly what the model receives
    assert REQUEST["user_prompt"] == build_prompt(ALERT)
    # and the triage persona is wired into the request
    # (pydantic-ai strips leading/trailing whitespace from instructions)
    assert REQUEST["instructions"].strip() == TRIAGE_INSTRUCTIONS.strip()
    assert result.output == ANALYSIS


def test_websearch_toolset_exposes_search_only() -> None:
    # no web_fetch: alert annotations are untrusted input, and fetching an
    # attacker-chosen URL would exfiltrate readable cluster data to the web
    # (websearch_tools is the FunctionToolset wrapped in a metadata wrapper)
    assert set(websearch_tools.wrapped.tools) == {"web_search"}


def test_agent_wires_tool_output_limits(tmp_path) -> None:
    # in pydantic-ai 2.56 the kubernetes toolset is absorbed into the code-mode
    # sandbox (only run_code is callable), so one giant run_code result can
    # otherwise blow the context window; oversized returns must be spilled
    agent = build_triage_agent(
        FunctionModel(fake_model), timeout=300, scratch_dir=tmp_path
    )
    limits = [
        cap
        for cap in leaf_capabilities(agent.root_capability)
        if isinstance(cap, ToolOutputLimits)
    ]
    assert len(limits) == 1
    assert limits[0].bands == [
        Band(over=48_000, action=Spill(then=Truncate(max_chars=48_000)))
    ]
    # spills land in a dedicated subdir of the scratch dir, pruned after an hour
    assert isinstance(limits[0].store, LocalFileStore)
    assert limits[0].store.base_dir == tmp_path / "alertagent-spills"


def test_chunk_keeps_lines_whole_and_respects_limit() -> None:
    # short text stays one chunk
    assert _chunk("a\nb\n", 10) == ["a\nb\n"]
    # splits across line boundaries, never exceeding the limit
    assert _chunk("abc\ndef\nghi\n", 5) == ["abc\n", "def\n", "ghi\n"]
    # a single line longer than the limit is hard-split
    assert _chunk("abcdefgh\nx", 5) == ["abcde", "fgh\nx"]


def test_chunk_round_trips_long_analysis() -> None:
    text = "# Root cause\n" + "x" * 5000 + "\n# Evidence\n" + "y" * 100
    chunks = _chunk(text, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == text


def test_recovery_retries_transient_and_unusable_output() -> None:
    assert _recovery_for(ModelAPIError("m", "timed out"))[1] is True
    for code in (408, 429, 500, 502, 503):
        message, retry = _recovery_for(ModelHTTPError(code, "m", None))
        assert retry is True
        assert str(code) in message
    # the native read_tool_result is the only non-sandboxed tool; its I/O
    # failures are environmental, so a fresh run may clear them
    assert _recovery_for(FileNotFoundError("spilled file"))[1] is True
    for msg in (
        "Invalid response from openai chat completions endpoint",
        "Exceeded maximum output retries (1)",
    ):
        assert _recovery_for(UnexpectedModelBehavior(msg))[1] is True


def test_recovery_does_not_retry_loop_burned_or_misconfigured_runs() -> None:
    assert _recovery_for(UsageLimitExceeded("request limit"))[1] is False
    # tool-loop and token-limit deaths (2.56 message wording) fail identically on retry
    assert _recovery_for(
        UnexpectedModelBehavior("Tool 'run_code' exceeded max retries count of 3")
    )[1] is False
    assert _recovery_for(
        UnexpectedModelBehavior(
            "Model token limit (16384) exceeded before any response was generated. "
            "Increase the `max_tokens` model setting."
        )
    )[1] is False
    assert _recovery_for(IncompleteToolCall("incomplete args"))[1] is False
    assert _recovery_for(ContentFilterError("blocked"))[1] is False
    assert _recovery_for(RunCancelled("run cancelled"))[1] is False
    assert _recovery_for(UserError("spill requires a workspace"))[1] is False
    for code in (400, 401, 403):
        assert _recovery_for(ModelHTTPError(code, "m", None))[1] is False


def test_recovery_names_suggested_model_on_404() -> None:
    message, retry = _recovery_for(
        ModelHTTPError(404, "gpt-5o", None, suggested_model_id="gpt-5")
    )
    assert retry is False
    assert "gpt-5o" in message
    assert "Did you mean 'gpt-5'?" in message


def test_recovery_fallback_reports_unknown_exceptions() -> None:
    message, retry = _recovery_for(ValueError("surprise"))
    assert retry is False
    assert "ValueError" in message


def test_backoff_defaults_to_five_seconds() -> None:
    assert _backoff(ModelAPIError("m", "timed out")) == 5.0
    assert _backoff(ModelHTTPError(500, "m", None)) == 5.0
    assert _backoff(ModelHTTPError(500, "m", None, headers={})) == 5.0
    assert _backoff(ModelHTTPError(500, "m", None, headers={"other": "7"})) == 5.0


def test_backoff_honours_and_clamps_retry_after() -> None:
    assert _backoff(ModelHTTPError(429, "m", None, headers={"retry-after": "7"})) == 7.0
    # cap: a quota-reset header must not hold a triage semaphore slot for minutes
    assert _backoff(ModelHTTPError(429, "m", None, headers={"retry-after": "3600"})) == 30.0
    assert _backoff(ModelHTTPError(429, "m", None, headers={"retry-after": "-3"})) == 0.0
    assert _backoff(ModelHTTPError(429, "m", None, headers={"retry-after": "soon"})) == 5.0


class _FakeThread:
    """Records everything the bot sends to the analysis thread."""

    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send(
        self, content: str | None = None, *, file: Any = None, **kwargs: Any
    ) -> discord.Message:
        self.sent.append(file if file is not None else content)
        return cast("discord.Message", None)

    def typing(self):
        @asynccontextmanager
        async def _typing():
            yield

        return _typing()


class _FakeMessage:
    def __init__(self, thread: _FakeThread) -> None:
        self._thread = thread

    async def create_thread(self, *, name: str, reason: str) -> _FakeThread:
        return self._thread


class FakeThreadChannel(discord.TextChannel):
    def __init__(self) -> None:  # skip discord's init; only isinstance matters
        self.thread = _FakeThread()

    async def send(self, content: Any = None, *, embed: Any = None, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage(self.thread)


async def _instant_sleep(_seconds: float) -> None:
    pass


@pytest.fixture
def no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(triage_module, "sleep", _instant_sleep)


def test_triage_retries_transient_transport_failure(tmp_path, no_sleep) -> None:
    attempts = {"n": 0}

    def flaky_model(messages, agent_info):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelAPIError("m", "connection reset")
        return ModelResponse([TextPart(ANALYSIS)])

    channel = FakeThreadChannel()
    asyncio.run(
        triage_module.triage_alert(channel, ALERT, FunctionModel(flaky_model), 300, tmp_path)
    )
    assert attempts["n"] == 2  # one retry after the first failure
    assert channel.thread.sent == [ANALYSIS]  # no error post; the analysis lands


def test_triage_gives_up_after_one_retry(tmp_path, no_sleep) -> None:
    attempts = {"n": 0}

    def dead_model(messages, agent_info):
        attempts["n"] += 1
        raise ModelAPIError("m", "connection reset")

    channel = FakeThreadChannel()
    with pytest.raises(ModelAPIError):
        asyncio.run(
            triage_module.triage_alert(channel, ALERT, FunctionModel(dead_model), 300, tmp_path)
        )
    assert attempts["n"] == 2  # exactly one retry, never more
    assert channel.thread.sent[0].startswith("Analysis failed:")


def test_triage_does_not_retry_misconfiguration(tmp_path, no_sleep) -> None:
    attempts = {"n": 0}

    def bad_key_model(messages, agent_info):
        attempts["n"] += 1
        raise ModelHTTPError(401, "m", {"error": "invalid api key"})

    channel = FakeThreadChannel()
    with pytest.raises(ModelHTTPError):
        asyncio.run(
            triage_module.triage_alert(channel, ALERT, FunctionModel(bad_key_model), 300, tmp_path)
        )
    assert attempts["n"] == 1  # a fresh run fails identically; don't waste it
    assert "401" in channel.thread.sent[0]
