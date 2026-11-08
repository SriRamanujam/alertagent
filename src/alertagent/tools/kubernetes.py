from functools import lru_cache
from typing import Any, List

from kubernetes.dynamic import DynamicClient, ResourceInstance, Subresource
import kubernetes
from kubernetes.client import ApiClient
from pydantic_ai.toolsets import FunctionToolset


@lru_cache
def _kubernetes_client() -> DynamicClient:
    try:
        return DynamicClient(
            ApiClient(configuration=kubernetes.config.load_incluster_config())
        )
    except kubernetes.config.ConfigException:
        return DynamicClient(
            ApiClient(configuration=kubernetes.config.load_kube_config())
        )


def get_kubernetes_resource(
    api_version: str,
    kind: str,
    name: str,
    namespace: str | None = None,
) -> dict | str:
    """Fetch a Kubernetes resource and return it as a dict"""
    if kind == "Secret":
        return "Secret access is forbidden by policy."

    resource: ResourceInstance = _kubernetes_client().resources.get(
        api_version=api_version, kind=kind
    )
    instance = resource.get(name=name, namespace=namespace)
    if isinstance(instance, ResourceInstance):
        return instance.to_dict()
    return instance


def _slim_item(item: dict) -> dict:
    """Reduce a listed object to the fields triage needs; drop spec/managedFields."""
    meta = item.get("metadata", {})
    return {
        "metadata": {
            "name": meta.get("name"),
            "namespace": meta.get("namespace"),
            "creationTimestamp": meta.get("creationTimestamp"),
            "labels": meta.get("labels"),
        },
        "status": item.get("status"),
    }


def list_kubernetes_resources(
    api_version: str,
    kind: str,
    namespace: str | None = None,
    limit: int = 100,
    continue_token: str | None = None,
) -> dict[str, Any]:
    """List one page of Kubernetes resources of a Kind.

    Returns {"items": [...], "continue": <token or None>}. Items are slimmed to
    metadata (name, namespace, creationTimestamp, labels) plus status; use
    get_kubernetes_resource for a full object. Pass the returned `continue`
    token back as `continue_token` for the next page; None means the last page.
    """
    resource: ResourceInstance = _kubernetes_client().resources.get(
        api_version=api_version, kind=kind
    )
    page_kwargs: dict[str, object] = {"limit": limit}
    if continue_token is not None:
        page_kwargs["_continue"] = continue_token
    body = resource.get(namespace=namespace, **page_kwargs).to_dict()
    return {
        "items": [_slim_item(item) for item in body["items"]],
        "continue": body.get("metadata", {}).get("continue"),
    }


def _get_pod_subresource(subresource: str) -> Subresource:
    return (
        _kubernetes_client()
        .resources.get(api_version="v1", kind="Pod")
        .subresources[subresource]
    )


def get_pod_logs(
    namespace: str,
    name: str,
    container: str | None = None,
    tail_lines: int = 200,
) -> str:
    """Get recent logs for a container of the given pod.

    Returns at most the last `tail_lines` lines (default 200); pass a larger
    value for more history. Logs are plain text, not JSON.

    container is required for pods with multiple containers (any container
    on the pod, including init containers); it may be omitted for
    single-container pods.
    """
    logs = _get_pod_subresource("log")

    # The dynamic client only forwards recognized kwargs, so pass container
    # and tailLines as explicit query params.
    query_params: list[tuple[str, str | int]] = [("tailLines", tail_lines)]
    if container:
        query_params.append(("container", container))

    return logs.get(name=name, namespace=namespace, query_params=query_params)


def get_resource_events(
    api_version: str,
    kind: str,
    name: str,
    namespace: str | None = None,
) -> List[dict]:
    """Get the events associated with a specific resource."""

    # Events are a standalone resource keyed by involvedObject; filtering on
    # name+kind+apiVersion keeps out other objects that share the name.
    events = _kubernetes_client().resources.get(api_version="v1", kind="Event")
    field_selector = f"involvedObject.name={name},involvedObject.kind={kind},involvedObject.apiVersion={api_version}"
    if namespace:
        field_selector += f",involvedObject.namespace={namespace}"
    return events.get(namespace=namespace, field_selector=field_selector).to_dict()[
        "items"
    ]


kubernetes_tools = FunctionToolset(
    tools=[
        get_kubernetes_resource,
        list_kubernetes_resources,
        get_pod_logs,
        get_resource_events,
    ]
).with_metadata(code_mode=True)
