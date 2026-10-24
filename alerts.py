"""Hot warnings: notice when a reading has stayed in the red for a while."""
from status import HOT

REPEAT_AFTER = 300  # seconds before warning again about the same reading


class AlertManager:
    def __init__(self):
        self.hot_since: dict[str, float] = {}
        self.last_warned: dict[str, float] = {}

    def update(self, name: str, color: str, now: float, after_seconds: float) -> tuple[bool, bool]:
        """Returns (alerting, warn_now). alerting = red for at least after_seconds;
        warn_now = also time to send a notification/sound for it."""
        if color != HOT:
            self.hot_since.pop(name, None)
            return False, False
        since = self.hot_since.setdefault(name, now)
        if now - since < after_seconds:
            return False, False
        warn = now - self.last_warned.get(name, float("-inf")) >= REPEAT_AFTER
        if warn:
            self.last_warned[name] = now
        return True, warn

    def reset(self):
        self.hot_since.clear()
