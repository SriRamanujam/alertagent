"""Raw webhook payload fixtures for tests.

Modeled on real Alertmanager output from an OKD cluster (grouped firing alert,
multi-alert resolved group, Watchdog); all values are made up. Shapes mirror
what ``alertmanager_ingest`` actually receives — including the absence of a
``fingerprint`` field.
"""

import copy

EXTERNAL_URL = "https://monitoring.example.test"

# Single firing alert with a rich label set (modeled on a cronjob backup alert).
FIRING_MESSAGE: dict = {
    "version": "4",
    "groupKey": '{}:{namespace="vault"}',
    "status": "firing",
    "receiver": "Discord",
    "commonLabels": {
        "prometheus": "openshift-monitoring/k8s",
        "namespace": "vault",
        "cronjob": "vault-dump-nightly",
        "alertname": "NightlyBackupCronjobUnsuccessful",
        "endpoint": "https-main",
        "job": "kube-state-metrics",
        "container": "kube-rbac-proxy-main",
        "service": "kube-state-metrics",
        "severity": "critical",
    },
    "commonAnnotations": {
        "message": (
            "The vault-dump-nightly backup job has not completed successfully for 14 days."
        )
    },
    "externalURL": EXTERNAL_URL,
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "NightlyBackupCronjobUnsuccessful",
                "job": "kube-state-metrics",
                "namespace": "vault",
                "severity": "critical",
                "service": "kube-state-metrics",
                "prometheus": "openshift-monitoring/k8s",
                "endpoint": "https-main",
                "container": "kube-rbac-proxy-main",
                "cronjob": "vault-dump-nightly",
            },
            "annotations": {
                "message": (
                    "The vault-dump-nightly backup job has not completed successfully for 14 days."
                )
            },
            "startsAt": "2026-10-15T08:45:18.955Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": f"{EXTERNAL_URL}/graph?g0.tab=1",
        }
    ],
}

# Group of resolved storage-health alerts (modeled on a Ceph mon/OSD down group).
RESOLVED_MESSAGE: dict = {
    "version": "4",
    "groupKey": "{}:{}",
    "status": "resolved",
    "receiver": "Discord",
    "commonLabels": {
        "prometheus": "openshift-monitoring/k8s",
        "openshift_io_alert_source": "platform",
        "type": "storage_default",
    },
    "commonAnnotations": {},
    "externalURL": EXTERNAL_URL,
    "alerts": [
        {
            "status": "resolved",
            "labels": {
                "openshift_io_alert_source": "platform",
                "alertname": "StorageMonDown",
                "type": "storage_default",
                "prometheus": "openshift-monitoring/k8s",
                "severity": "warning",
            },
            "annotations": {
                "description": (
                    "You have 1 monitor down. Quorum is still intact, but the loss of an "
                    "additional monitor will make your cluster inoperable."
                ),
                "summary": "One or more monitors down",
                "documentation": "https://docs.example.test/storage/health#mon-down",
            },
            "startsAt": "2026-10-15T05:02:14.455Z",
            "endsAt": "2026-10-15T05:06:14.455Z",
            "generatorURL": f"{EXTERNAL_URL}/graph?g0.tab=1",
        },
        {
            "status": "resolved",
            "labels": {
                "type": "storage_default",
                "oid": "1.2.3.4.5",
                "severity": "critical",
                "prometheus": "openshift-monitoring/k8s",
                "alertname": "StorageOSDDownHigh",
                "openshift_io_alert_source": "platform",
            },
            "annotations": {
                "description": "11.11% or 1 of 9 OSDs are down (>= 10%).",
                "summary": "More than 10% of OSDs are down",
            },
            "startsAt": "2026-10-15T05:01:54.380Z",
            "endsAt": "2026-10-15T05:06:24.380Z",
            "generatorURL": f"{EXTERNAL_URL}/graph?g0.tab=1",
        },
    ],
}

# The Watchdog alert: always firing, must never be triaged or cached.
WATCHDOG_MESSAGE: dict = {
    "version": "4",
    "groupKey": '{}:{namespace="openshift-monitoring"}',
    "status": "firing",
    "receiver": "Discord",
    "commonLabels": {
        "openshift_io_alert_source": "platform",
        "namespace": "openshift-monitoring",
        "severity": "none",
        "prometheus": "openshift-monitoring/k8s",
        "alertname": "Watchdog",
    },
    "commonAnnotations": {
        "summary": "An alert that should always be firing to certify that Alertmanager is working properly.",
        "description": "This is an alert meant to ensure that the entire alerting pipeline is functional.",
    },
    "externalURL": EXTERNAL_URL,
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "prometheus": "openshift-monitoring/k8s",
                "alertname": "Watchdog",
                "namespace": "openshift-monitoring",
                "severity": "none",
                "openshift_io_alert_source": "platform",
            },
            "annotations": {
                "description": "This is an alert meant to ensure that the entire alerting pipeline is functional.",
                "summary": (
                    "An alert that should always be firing to certify that Alertmanager is working properly."
                ),
            },
            "startsAt": "2026-09-23T00:15:11.736Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": f"{EXTERNAL_URL}/graph?g0.tab=1",
        }
    ],
}

# The raw firing alert, for alert-level fixtures.
FIRING_ALERT: dict = FIRING_MESSAGE["alerts"][0]


def clone_message(base: dict, **alert_overrides: str) -> dict:
    """Deepcopy a webhook message, overriding alert fields (status, startsAt, endsAt)."""
    out = copy.deepcopy(base)
    for a in out["alerts"]:
        a.update(alert_overrides)
    out["status"] = out["alerts"][0]["status"]
    return out
