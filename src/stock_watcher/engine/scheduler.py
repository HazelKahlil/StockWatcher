from __future__ import annotations

from datetime import datetime, time


class ReplaySchedule:
    """Injectable-clock schedule; timestamps are never inferred from wall clock."""

    _times = (time(9, 45), time(14, 50))

    def due(self, now: datetime) -> bool:
        return now.timetz().replace(tzinfo=None) in self._times
