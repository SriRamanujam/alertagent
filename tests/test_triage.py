import asyncio

from pydantic_ai.capabilities.abstract import leaf_capabilities
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import ToolOutputLimits
from pydantic_ai_harness.tool_output_limits import Band, LocalFileStore, Spill, Truncate

from alertagent.alertmanager import AlertmanagerAlert
from alertagent.tools.websearch import websearch_tools
from alertagent.triage import (
    TRIAGE_INSTRUCTIONS,
    _chunk,
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
