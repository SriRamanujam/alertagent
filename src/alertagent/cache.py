"""In-memory de-duplication of firing alerts.

Alertmanager resends an alert on every repeat_interval and on group changes.
The dedup key hashes sorted labels + startsAt as the unique key of an alert,
and caches.
"""

import hashlib
import json
from collections import OrderedDict

from .alertmanager import AlertmanagerAlert

MAX_CACHE_SIZE = 10_000


def alert_key(alert: AlertmanagerAlert) -> str:
    """Stable dedup key: hash of sorted labels + startsAt."""
    labels = json.dumps(alert.labels, sort_keys=True)
    return hashlib.sha256(
        f"{labels}|{alert.starts_at.isoformat()}".encode()
    ).hexdigest()


class AlertCache:
    """Tracks currently-firing alerts. Evicts the oldest alert past MAX_CACHE_SIZE."""

    def __init__(self, max_size: int = MAX_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._seen: OrderedDict[str, None] = OrderedDict()

    def seen(self, alert: AlertmanagerAlert) -> bool:
        """
        True if the alert was already cached, False otherwise.
        If alert is not in cache, this will insert it.
        """
        key = alert_key(alert)
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    def forget(self, alert: AlertmanagerAlert) -> None:
        """Remove the alert from the cache"""
        self._seen.pop(alert_key(alert), None)


# Singleton, same style as Discord.get_client(); safe under asyncio (single event loop).
cache = AlertCache()
