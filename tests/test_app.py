"""Endpoint-level dedup: resend of a firing alert must not re-post or re-triage."""

import asyncio
import copy
from typing import Any, cast

import discord
import pytest

import alertagent.app as app_module
import alertagent.session as session_module
from alertagent.alertmanager import AlertmanagerMessage
from alertagent.cache import AlertCache
from alertagent.config import Settings
from alertagent.session import Discord, Model
from fixtures import (
    FIRING_ALERT,
    FIRING_MESSAGE,
    RESOLVED_MESSAGE,
    WATCHDOG_MESSAGE,
    clone_message,
)


class FakeTextChannel(discord.TextChannel):
    def __init__(self) -> None:  # skip discord's init; only isinstance matters
        self.sent_embeds: list[discord.Embed] = []

    # The app only calls `chan.send(embed=...)` and ignores the return value;
    # the remaining real-signature params are absorbed, never exercised.
    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        **kwargs: Any,
    ) -> discord.Message:
        if embed is not None:
            self.sent_embeds.append(embed)
        return cast("discord.Message", None)


@pytest.fixture
def wired(monkeypatch) -> dict:
    cache = AlertCache()
    channel = FakeTextChannel()
    triaged: list = []

    async def fake_triage(channel, alert, model, timeout, scratch_dir):
        triaged.append(alert)

    class FakeBot:
        async def fetch_channel(self, _id):
            return channel

    monkeypatch.setattr(app_module, "cache", cache)
    monkeypatch.setattr(app_module, "triage_alert", fake_triage)
    # fresh per test: the real semaphore binds to whichever event loop first
    # touches it, and each test below runs its own loop
    monkeypatch.setattr(app_module, "_triage_semaphore", asyncio.Semaphore(3))
    monkeypatch.setattr(Discord, "get_client", classmethod(lambda cls: FakeBot()))
    # Model.get() reads settings from session, not app; patch both so tests
    # never touch real credentials
    settings = Settings(
        discord_bot_token="t", discord_channel_id=1, openai_api_key="k"
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)
    return {"cache": cache, "channel": channel, "triaged": triaged}


def ingest(wired: dict, payload: dict) -> None:
    settings = app_module.get_settings()
    asyncio.run(
        app_module.alertmanager_ingest(
            AlertmanagerMessage.model_validate(payload), settings
        )
    )


def test_model_is_built_once_and_closed(wired, monkeypatch) -> None:
    # the OpenAI client is a process-wide singleton: one httpx session,
    # closed on shutdown, rebuilt after close
    monkeypatch.setattr(Model, "_client", None)
    monkeypatch.setattr(Model, "_model", None)
    m1 = Model.get()
    assert Model.get() is m1
    asyncio.run(Model.on_shutdown())
    assert Model._client is None and Model._model is None
    m2 = Model.get()
    assert m2 is not m1
    asyncio.run(Model.on_shutdown())  # leave clean


def test_firing_resend_is_ignored(wired) -> None:
    ingest(wired, FIRING_MESSAGE)
    ingest(wired, FIRING_MESSAGE)  # repeat-interval resend of the same alert
    assert len(wired["triaged"]) == 1  # second webhook starts no agent
    assert len(wired["channel"].sent_embeds) == 0  # triage posts, not the endpoint


def test_resolved_completion_is_sent_and_forgets(wired) -> None:
    ingest(wired, FIRING_MESSAGE)
    completion = clone_message(
        FIRING_MESSAGE, status="resolved", endsAt="2026-10-15T09:45:18.955Z"
    )
    ingest(wired, completion)
    assert len(wired["channel"].sent_embeds) == 1  # completion is always sent
    # refire after resolution triages again
    ingest(wired, clone_message(FIRING_MESSAGE, startsAt="2026-10-15T09:50:00.000Z"))
    assert len(wired["triaged"]) == 2


def test_resolved_group_sends_every_alert(wired) -> None:
    # a multi-alert resolved group posts one completion embed per alert
    ingest(wired, RESOLVED_MESSAGE)
    assert len(wired["channel"].sent_embeds) == len(RESOLVED_MESSAGE["alerts"]) == 2
    assert len(wired["triaged"]) == 0


def test_watchdog_is_ignored_and_never_triaged(wired) -> None:
    ingest(wired, WATCHDOG_MESSAGE)
    ingest(wired, WATCHDOG_MESSAGE)
    assert len(wired["triaged"]) == 0
    assert len(wired["channel"].sent_embeds) == 0
    # a real firing alert is still triaged afterwards
    ingest(wired, FIRING_MESSAGE)
    assert len(wired["triaged"]) == 1


def test_triage_concurrency_is_capped(wired, monkeypatch) -> None:
    # a burst of distinct alerts must not all hit the LLM endpoint at once;
    # the extras queue on the semaphore and still run
    running = {"n": 0, "peak": 0}
    release = asyncio.Event()

    async def slow_triage(channel, alert, model, timeout, scratch_dir):
        running["n"] += 1
        running["peak"] = max(running["peak"], running["n"])
        await release.wait()
        running["n"] -= 1
        wired["triaged"].append(alert)

    monkeypatch.setattr(app_module, "triage_alert", slow_triage)

    burst = copy.deepcopy(FIRING_MESSAGE)
    burst["alerts"] = [
        {**FIRING_ALERT, "labels": {**FIRING_ALERT["labels"], "alertname": f"Burst{i}"}}
        for i in range(5)
    ]

    async def scenario():
        settings = app_module.get_settings()
        await app_module.alertmanager_ingest(
            AlertmanagerMessage.model_validate(burst), settings
        )
        await asyncio.sleep(0)  # let all 5 triage tasks start
        assert running["peak"] <= 3
        release.set()
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending)

    asyncio.run(scenario())
    assert running["peak"] == 3  # exactly 3 ran at once, 2 waited on the semaphore
    assert len(wired["triaged"]) == 5  # queued alerts still got triaged


def test_failed_triage_retries_on_resend(wired, monkeypatch) -> None:
    # a transient triage failure must not mute the alert for its whole firing life
    calls = {"n": 0}

    async def failing_triage(channel, alert, model, timeout, scratch_dir):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LLM endpoint down")
        wired["triaged"].append(alert)

    monkeypatch.setattr(app_module, "triage_alert", failing_triage)
    ingest(wired, FIRING_MESSAGE)  # first attempt fails; cache entry forgotten
    assert len(wired["triaged"]) == 0
    ingest(wired, FIRING_MESSAGE)  # resend must retry the triage
    assert len(wired["triaged"]) == 1
