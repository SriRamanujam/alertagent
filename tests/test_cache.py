from datetime import timedelta

from alertagent.alertmanager import AlertmanagerAlert
from alertagent.cache import AlertCache, alert_key
from fixtures import FIRING_ALERT


def make_alert(
    labels: dict[str, str] | None = None,
    starts_at: str | None = None,
    fingerprint: str = "",
) -> AlertmanagerAlert:
    """An alert based on the real-webhook fixture, with optional overrides."""
    payload = dict(FIRING_ALERT)
    payload["fingerprint"] = fingerprint
    if labels is not None:
        payload["labels"] = labels
    if starts_at is not None:
        payload["startsAt"] = starts_at
    return AlertmanagerAlert.model_validate(payload)


def test_alert_key_stable_under_label_order() -> None:
    labels = FIRING_ALERT["labels"]
    a = make_alert(labels=labels)
    b = make_alert(labels=dict(reversed(list(labels.items()))))
    assert alert_key(a) == alert_key(b)


def test_alert_key_changes_with_label_value_or_start() -> None:
    base = make_alert()
    changed = dict(FIRING_ALERT["labels"])
    changed["severity"] = "warning"
    assert alert_key(base) != alert_key(make_alert(labels=changed))
    refired = base.model_copy(
        update={"starts_at": base.starts_at + timedelta(seconds=1)}
    )
    assert alert_key(base) != alert_key(refired)


def test_seen_dedupes_resend() -> None:
    cache = AlertCache()
    assert cache.seen(make_alert()) is False  # first sighting
    assert cache.seen(make_alert()) is True  # repeat-interval resend


def test_forget_allows_refire_after_resolution() -> None:
    cache = AlertCache()
    alert = make_alert()
    assert cache.seen(alert) is False
    cache.forget(alert)  # resolved
    assert cache.seen(alert) is False  # refire triages again
    assert cache.seen(make_alert(starts_at="2026-10-15T09:45:18.955Z")) is False


def test_fifo_eviction_drops_oldest() -> None:
    cache = AlertCache(max_size=3)
    alerts = [make_alert(starts_at=f"2026-10-15T08:{45 + i}:18.955Z") for i in range(3)]
    for a in alerts:
        assert cache.seen(a) is False
    # fourth entry evicts the oldest
    assert cache.seen(make_alert(starts_at="2026-10-15T13:00:00.000Z")) is False
    assert cache.seen(alerts[1]) is True  # still cached
    assert cache.seen(alerts[2]) is True
    assert cache.seen(alerts[0]) is False  # evicted -> treated as new
