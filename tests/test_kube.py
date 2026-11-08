import asyncio
from unittest import mock
from unittest.mock import MagicMock

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from kubernetes.dynamic.resource import ResourceInstance

from alertagent.tools import kubernetes as kube
from alertagent.triage import build_triage_agent


def _client_with_resource() -> tuple[MagicMock, MagicMock]:
    """Fake DynamicClient: client.resources.get(...) returns a resource mock."""
    client = MagicMock()
    resource = MagicMock()
    client.resources.get.return_value = resource
    return client, resource


def _instance(client: MagicMock, payload: dict) -> ResourceInstance:
    """Wrap a payload the way the real dynamic client does."""
    return ResourceInstance(client, dict(payload))


def test_get_resource_passes_args_and_returns_dict() -> None:
    client, resource = _client_with_resource()
    payload = {
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "monitoring"},
    }
    resource.get.return_value = _instance(client, payload)

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        result = kube.get_kubernetes_resource(
            "apps/v1", "Deployment", "api", namespace="monitoring"
        )

    client.resources.get.assert_called_once_with(
        api_version="apps/v1", kind="Deployment"
    )
    resource.get.assert_called_once_with(name="api", namespace="monitoring")
    assert result == payload


def test_get_resource_without_namespace() -> None:
    client, resource = _client_with_resource()
    resource.get.return_value = _instance(client, {"kind": "Namespace"})

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        kube.get_kubernetes_resource("v1", "Namespace", "kube-system")

    resource.get.assert_called_once_with(name="kube-system", namespace=None)


def test_get_pod_logs_without_container() -> None:
    client, resource = _client_with_resource()
    # The real dynamic client returns a plain string for the `log` subresource.
    resource.subresources["log"].get.return_value = "line one\nline two"

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        result = kube.get_pod_logs("monitoring", "web")

    client.resources.get.assert_called_once_with(api_version="v1", kind="Pod")
    # single-container pod: no container query param, but the default tail is always sent
    # so the model can never pull an unbounded log
    resource.subresources["log"].get.assert_called_once_with(
        name="web", namespace="monitoring", query_params=[("tailLines", 200)]
    )
    assert result == "line one\nline two"


def test_get_pod_logs_with_container() -> None:
    client, resource = _client_with_resource()
    resource.subresources["log"].get.return_value = "sidecar line"

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        result = kube.get_pod_logs("monitoring", "web", container="sidecar")

    # the dynamic client only forwards recognized kwargs, so container and
    # tailLines go as query params
    resource.subresources["log"].get.assert_called_once_with(
        name="web",
        namespace="monitoring",
        query_params=[("tailLines", 200), ("container", "sidecar")],
    )
    assert result == "sidecar line"


def test_get_pod_logs_custom_tail() -> None:
    client, resource = _client_with_resource()
    resource.subresources["log"].get.return_value = ""

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        kube.get_pod_logs("monitoring", "web", tail_lines=50)

    resource.subresources["log"].get.assert_called_once_with(
        name="web", namespace="monitoring", query_params=[("tailLines", 50)]
    )


def test_list_resources_returns_items() -> None:
    client, resource = _client_with_resource()
    items = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "monitoring"}}
    ]
    resource.get.return_value = _instance(
        client,
        {
            "apiVersion": "v1",
            "kind": "NamespaceList",
            "metadata": {"continue": "tok-2"},
            "items": items,
        },
    )

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        result = kube.list_kubernetes_resources(
            "v1", "Namespace", namespace="monitoring"
        )

    client.resources.get.assert_called_once_with(api_version="v1", kind="Namespace")
    # paged: the default limit is forwarded to the API
    resource.get.assert_called_once_with(namespace="monitoring", limit=100)
    # items are slimmed to metadata + status, and the continue token is surfaced
    assert result == {
        "items": [
            {
                "metadata": {
                    "name": "monitoring",
                    "namespace": None,
                    "creationTimestamp": None,
                    "labels": None,
                },
                "status": None,
            }
        ],
        "continue": "tok-2",
    }


def test_list_resources_forwards_continue_token() -> None:
    client, resource = _client_with_resource()
    resource.get.return_value = _instance(
        client, {"apiVersion": "v1", "kind": "NamespaceList", "items": []}
    )

    with mock.patch.object(kube, "_kubernetes_client", return_value=client):
        result = kube.list_kubernetes_resources(
            "v1", "Namespace", limit=50, continue_token="tok-2"
        )

    # the dynamic client takes the continue token as `_continue`
    resource.get.assert_called_once_with(namespace=None, limit=50, _continue="tok-2")
    # no `continue` in the response metadata means the last page
    assert result == {"items": [], "continue": None}


def fake_model(messages, agent_info):
    return ModelResponse([TextPart("ok")])


def test_agent_registers_kubernetes_tools(tmp_path) -> None:
    agent = build_triage_agent(FunctionModel(fake_model), 30, scratch_dir=tmp_path)
    asyncio.run(agent.run("firing alert"))
    # CodeMode(tools={"code_mode": True}) absorbs the kubernetes toolset into
    # the run_code sandbox, so the individual functions never appear as native
    # tools -- assert the toolset itself is registered on the agent instead.
    assert kube.kubernetes_tools in agent.toolsets
