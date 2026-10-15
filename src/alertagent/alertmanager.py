"""Pydantic models for the alertmanager webhook payload sent to alert receivers.

Converted from the alertmanager Go code:
- notify/webhook/webhook.go: ``Message`` (embeds ``template.Data``)
- template/template.go: ``Data``, ``Alert``
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerAlert(BaseModel):
    """One alert in the payload (Go ``template.Alert``)."""

    model_config = ConfigDict(populate_by_name=True)

    status: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime = Field(alias="endsAt")
    generator_url: str = Field(alias="generatorURL")
    # Added by the newer webhook protocol; older payloads omit it.
    fingerprint: str = ""


class AlertmanagerMessage(BaseModel):
    """The JSON payload webhook receivers get (Go ``webhook.Message`` with the
    embedded ``template.Data`` flattened)."""

    model_config = ConfigDict(populate_by_name=True)

    version: str
    group_key: str = Field(alias="groupKey")
    truncated_alerts: int = Field(default=0, alias="truncatedAlerts")

    # template.Data fields
    receiver: str
    status: str
    alerts: list[AlertmanagerAlert]
    # Added by the newer webhook protocol; older payloads omit these.
    notification_reason: str | None = None
    group_labels: dict[str, str] = Field(default_factory=dict, alias="groupLabels")
    common_labels: dict[str, str] = Field(alias="commonLabels")
    common_annotations: dict[str, str] = Field(alias="commonAnnotations")
    route_labels: dict[str, str] = Field(default_factory=dict, alias="routeLabels")
    external_url: str = Field(alias="externalURL")
