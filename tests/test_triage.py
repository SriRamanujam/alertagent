import asyncio

from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from alertagent.alertmanager import AlertmanagerAlert
from alertagent.triage import TRIAGE_INSTRUCTIONS, build_prompt, build_triage_agent

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


def test_agent_run_with_function_model() -> None:
    agent = build_triage_agent(FunctionModel(fake_model))
    result = asyncio.run(agent.run(build_prompt(ALERT)))
    # the prompt built from the alert is exactly what the model receives
    assert REQUEST["user_prompt"] == build_prompt(ALERT)
    # and the triage persona is wired into the request
    # (pydantic-ai strips leading/trailing whitespace from instructions)
    assert REQUEST["instructions"].strip() == TRIAGE_INSTRUCTIONS.strip()
    assert result.output == ANALYSIS
