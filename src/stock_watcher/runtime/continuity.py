from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from stock_watcher.domain import SHANGHAI

TRADING_GAP_THRESHOLD_SECONDS = 90.0
_MORNING = (time(9, 30), time(11, 30))
_AFTERNOON = (time(13, 0), time(15, 0))


@dataclass(frozen=True, slots=True)
class ScanGap:
    start: datetime
    end: datetime
    total_seconds: float
    trading_seconds: float
    classification: str

    @property
    def is_reportable_trading_gap(self) -> bool:
        return self.trading_seconds > TRADING_GAP_THRESHOLD_SECONDS


def analyze_scan_gaps(
    timestamps: Iterable[datetime],
    *,
    runtime_sessions: Sequence[Mapping[str, object]] = (),
    runtime_events: Sequence[Mapping[str, object]] = (),
) -> tuple[ScanGap, ...]:
    """Return deterministic scan-gap facts with trading-time overlap.

    The previous implementation only reported the single longest wall-clock gap.
    A normal 90-minute lunch break could therefore hide a much more important
    42-minute gap inside the afternoon session.  This helper retains the overall
    gap and independently measures the exchange-session overlap for every gap.
    """

    normalized = sorted({_shanghai(value) for value in timestamps})
    sleep_intervals = _sleep_intervals(runtime_events, normalized[-1] if normalized else None)
    session_downtimes = _session_downtimes(runtime_sessions)
    output: list[ScanGap] = []
    for previous, current in zip(normalized, normalized[1:]):
        total = (current - previous).total_seconds()
        if total <= 0:
            continue
        trading = _trading_overlap_seconds(previous, current)
        classification = _classify_gap(
            previous,
            current,
            trading_seconds=trading,
            sleep_intervals=sleep_intervals,
            session_downtimes=session_downtimes,
        )
        output.append(
            ScanGap(
                start=previous,
                end=current,
                total_seconds=total,
                trading_seconds=trading,
                classification=classification,
            )
        )
    return tuple(output)


def continuity_gap_summary_parts(gaps: Sequence[ScanGap]) -> tuple[str, ...]:
    """Human-readable gap evidence suitable for SQLite summaries and PDFs."""

    if not gaps:
        return ()
    parts: list[str] = []
    longest = max(gaps, key=lambda item: item.total_seconds)
    parts.append(
        "最长无扫描间隔"
        f"{format_duration(longest.total_seconds)}"
        f"（{_range_text(longest)}，{longest.classification}）"
    )
    all_trading_gaps = [item for item in gaps if item.trading_seconds > 0]
    if all_trading_gaps:
        longest_trading = max(
            all_trading_gaps,
            key=lambda item: item.trading_seconds,
        )
        parts.append(
            "最长交易时段无扫描间隔"
            f"{format_duration(longest_trading.trading_seconds)}"
            f"（{_range_text(longest_trading)}，{longest_trading.classification}）"
        )
    trading_gaps = sorted(
        (item for item in gaps if item.is_reportable_trading_gap),
        key=lambda item: item.start,
    )
    if trading_gaps:
        rendered = "、".join(
            f"{_range_text(item)}"
            f"（交易时段{format_duration(item.trading_seconds)}，{item.classification}）"
            for item in trading_gaps
        )
        parts.append(f"交易时段超90秒空窗{len(trading_gaps)}段：{rendered}")
    return tuple(parts)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI)
    return value.astimezone(SHANGHAI)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _shanghai(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _shanghai(parsed)


def _trading_overlap_seconds(start: datetime, end: datetime) -> float:
    total = 0.0
    current_date = start.date()
    while current_date <= end.date():
        for block_start, block_end in (_MORNING, _AFTERNOON):
            left = datetime.combine(current_date, block_start, tzinfo=SHANGHAI)
            right = datetime.combine(current_date, block_end, tzinfo=SHANGHAI)
            total += _overlap_seconds(start, end, left, right)
        current_date += timedelta(days=1)
    return total


def _sleep_intervals(
    events: Sequence[Mapping[str, object]],
    last_timestamp: datetime | None,
) -> tuple[tuple[datetime, datetime], ...]:
    parsed: list[tuple[datetime, str]] = []
    for event in events:
        timestamp = _parse_datetime(event.get("occurred_at"))
        event_type = str(event.get("event_type", ""))
        if timestamp is not None and event_type in {"sleep_detected", "wake_detected"}:
            parsed.append((timestamp, event_type))
    parsed.sort()
    output: list[tuple[datetime, datetime]] = []
    sleep_at: datetime | None = None
    for timestamp, event_type in parsed:
        if event_type == "sleep_detected":
            sleep_at = timestamp
        elif sleep_at is not None and timestamp > sleep_at:
            output.append((sleep_at, timestamp))
            sleep_at = None
    if sleep_at is not None and last_timestamp is not None and last_timestamp > sleep_at:
        output.append((sleep_at, last_timestamp))
    return tuple(output)


def _session_downtimes(
    sessions: Sequence[Mapping[str, object]],
) -> tuple[tuple[datetime, datetime], ...]:
    parsed: list[tuple[datetime, datetime | None]] = []
    for session in sessions:
        started = _parse_datetime(session.get("started_at"))
        ended = _parse_datetime(session.get("ended_at"))
        if started is not None:
            parsed.append((started, ended))
    parsed.sort(key=lambda item: item[0])
    output: list[tuple[datetime, datetime]] = []
    for (_, previous_end), (next_start, _) in zip(parsed, parsed[1:]):
        if previous_end is not None and next_start > previous_end:
            output.append((previous_end, next_start))
    return tuple(output)


def _classify_gap(
    start: datetime,
    end: datetime,
    *,
    trading_seconds: float,
    sleep_intervals: Sequence[tuple[datetime, datetime]],
    session_downtimes: Sequence[tuple[datetime, datetime]],
) -> str:
    if any(_overlap_seconds(start, end, left, right) > 0 for left, right in sleep_intervals):
        return "睡眠"
    if any(
        _overlap_seconds(start, end, left, right) > 0
        for left, right in session_downtimes
    ):
        return "进程退出"
    if _is_midday_break(start, end, trading_seconds):
        return "午休"
    if trading_seconds > 0:
        return "交易时段内，原因未记录"
    return "非交易时段"


def _is_midday_break(start: datetime, end: datetime, trading_seconds: float) -> bool:
    if start.date() != end.date() or trading_seconds > TRADING_GAP_THRESHOLD_SECONDS:
        return False
    lunch_start = datetime.combine(start.date(), time(11, 30), tzinfo=SHANGHAI)
    lunch_end = datetime.combine(start.date(), time(13, 0), tzinfo=SHANGHAI)
    return _overlap_seconds(start, end, lunch_start, lunch_end) > 0


def _overlap_seconds(
    start: datetime,
    end: datetime,
    left: datetime,
    right: datetime,
) -> float:
    overlap_start = max(start, left)
    overlap_end = min(end, right)
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds()


def _range_text(gap: ScanGap) -> str:
    if gap.start.date() == gap.end.date():
        return f"{gap.start:%H:%M:%S}→{gap.end:%H:%M:%S}"
    return f"{gap.start:%m-%d %H:%M:%S}→{gap.end:%m-%d %H:%M:%S}"
