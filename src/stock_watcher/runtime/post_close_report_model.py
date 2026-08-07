from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stock_watcher.build_info import source_commit

if TYPE_CHECKING:
    from stock_watcher.storage import SQLiteStore


LOCAL_FALLBACK_SOURCE_VERSION = "daily-summary-local-fallback-v2"
LOCAL_FALLBACK_RENDERER_VERSION = "local-fallback-brief-v1"
FULL_MARKET_RENDERER_VERSION = "research-brief-v1"
PDF_MANIFEST_VERSION = "pdf-manifest-v1"


@dataclass(frozen=True, slots=True)
class LocalFallbackCandidate:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    level: str
    sector: str | None
    sector_type: str | None
    score: float | None
    reasons: tuple[str, ...]
    source_ts: str | None
    selection_source: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalFallbackAlert:
    trigger_type: str
    state: str
    displayed_at: str | None
    candidate_names: tuple[str, ...]
    candidate_codes: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LocalFallbackReport:
    trade_date: str
    generated_at: str
    report_mode: str
    source_version: str
    source_generated_at: str
    source_commit: str
    alert_count: int
    top3: tuple[LocalFallbackCandidate, ...]
    top3_source: str | None
    alerts: tuple[LocalFallbackAlert, ...]
    scan_count: int
    healthy_scan_count: int
    minimum_coverage: float | None
    maximum_coverage: float | None
    runtime_session_count: int
    restart_count: int
    sleep_count: int
    wake_count: int
    concept_status: str
    continuity: str
    market_limitation: str
    fund_summary: str
    summary_text: str

    def as_record(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "top3": [candidate.as_record() for candidate in self.top3],
            "alerts": [alert.as_record() for alert in self.alerts],
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> LocalFallbackReport:
        nested = value.get("local_fallback_report")
        source: Mapping[str, Any] = nested if isinstance(nested, Mapping) else value
        candidates = tuple(
            _candidate_from_mapping(item)
            for item in _sequence(source.get("top3"))
            if isinstance(item, Mapping)
        )
        alerts = tuple(
            _alert_from_mapping(item)
            for item in _sequence(source.get("alerts"))
            if isinstance(item, Mapping)
        )
        report = cls(
            trade_date=str(source.get("trade_date", "")),
            generated_at=str(source.get("generated_at", "")),
            report_mode=str(source.get("report_mode", "local_fallback")),
            source_version=str(source.get("source_version", value.get("version", ""))),
            source_generated_at=str(
                source.get("source_generated_at", value.get("generated_at", ""))
            ),
            source_commit=str(source.get("source_commit", "")),
            alert_count=int(source.get("alert_count", value.get("alert_count", 0)) or 0),
            top3=candidates,
            top3_source=_optional_text(source.get("top3_source")),
            alerts=alerts,
            scan_count=int(source.get("scan_count", 0) or 0),
            healthy_scan_count=int(source.get("healthy_scan_count", 0) or 0),
            minimum_coverage=_optional_float(source.get("minimum_coverage")),
            maximum_coverage=_optional_float(source.get("maximum_coverage")),
            runtime_session_count=int(source.get("runtime_session_count", 0) or 0),
            restart_count=int(source.get("restart_count", 0) or 0),
            sleep_count=int(source.get("sleep_count", 0) or 0),
            wake_count=int(source.get("wake_count", 0) or 0),
            concept_status=str(source.get("concept_status", "未取得概念缓存状态")),
            continuity=str(source.get("continuity", value.get("health_summary", ""))),
            market_limitation=str(
                source.get(
                    "market_limitation",
                    "未取得完整盘后全市场统计；本页使用当天本地实时扫描与提醒记录。",
                )
            ),
            fund_summary=str(
                source.get("fund_summary", value.get("fund_summary", "资金未确认"))
            ),
            summary_text=str(source.get("summary_text", value.get("summary_text", ""))),
        )
        validate_local_fallback_report(report)
        return report


def build_local_fallback_report(
    store: SQLiteStore,
    summary: Mapping[str, Any],
    *,
    now: datetime,
    source_commit_value: str | None = None,
) -> LocalFallbackReport:
    """Build a local-only report from durable scan, alert and runtime evidence."""
    trade_date = str(summary.get("trade_date", now.date().isoformat()))[:10]
    history = [
        row
        for row in store.list_alert_history(now=now, days=2)
        if str(row.get("displayed_at", "")).startswith(trade_date)
    ]
    history.sort(key=lambda row: str(row.get("displayed_at", "")))
    tasks = store.list_automation_tasks(trade_date)
    runs = store.list_scan_runs(trade_date)
    healthy_runs = [
        row
        for row in runs
        if str(row.get("health", "")) == "HEALTHY"
        and _parse_datetime(row.get("completed_at")) is not None
    ]
    latest_healthy = [
        row
        for row in healthy_runs
        if _is_before_three_pm(row.get("completed_at"), trade_date)
        and row.get("stable_batch_json")
    ]

    scheduled_1445 = _first_alert(history, "scheduled-14:45")
    latest_alert = history[-1] if history else None
    top3: tuple[LocalFallbackCandidate, ...] = ()
    top3_source: str | None = None
    if scheduled_1445 is not None:
        top3 = _candidates_from_payload(
            scheduled_1445.get("payload_json"), "scheduled_14_45"
        )
        top3_source = "scheduled_14_45" if top3 else None
    if not top3 and latest_healthy:
        top3 = _candidates_from_payload(
            latest_healthy[-1].get("stable_batch_json"), "latest_healthy_scan"
        )
        top3_source = "latest_healthy_scan" if top3 else None
    if not top3 and latest_alert is not None:
        top3 = _candidates_from_payload(
            latest_alert.get("payload_json"), "latest_alert"
        )
        top3_source = "latest_alert" if top3 else None

    alerts = _build_alert_timeline(tasks, history)
    coverage = [
        float(row["coverage_ratio"])
        for row in runs
        if isinstance(row.get("coverage_ratio"), (int, float))
    ]
    sessions = store.list_runtime_sessions(trade_date)
    sleep_count = 0
    wake_count = 0
    for session in sessions:
        for event in store.list_runtime_events(str(session.get("session_id", ""))):
            if not str(event.get("occurred_at", "")).startswith(trade_date):
                continue
            if event.get("event_type") == "sleep_detected":
                sleep_count += 1
            elif event.get("event_type") == "wake_detected":
                wake_count += 1

    continuity = _continuity_text(summary, runs, sessions, sleep_count, wake_count)
    report = LocalFallbackReport(
        trade_date=trade_date,
        generated_at=now.isoformat(),
        report_mode="local_fallback",
        source_version=LOCAL_FALLBACK_SOURCE_VERSION,
        source_generated_at=str(summary.get("generated_at", "")),
        source_commit=source_commit_value or source_commit(),
        alert_count=len(history),
        top3=top3[:3],
        top3_source=top3_source,
        alerts=alerts,
        scan_count=len(runs),
        healthy_scan_count=len(healthy_runs),
        minimum_coverage=min(coverage) if coverage else None,
        maximum_coverage=max(coverage) if coverage else None,
        runtime_session_count=len(sessions),
        restart_count=max(0, len(sessions) - 1),
        sleep_count=sleep_count,
        wake_count=wake_count,
        concept_status=_concept_status(str(summary.get("health_summary", ""))),
        continuity=continuity,
        market_limitation=(
            "未取得完整盘后全市场统计；本页使用当天本地实时扫描与提醒记录。"
        ),
        fund_summary=str(
            summary.get(
                "fund_summary",
                "资金未确认，本日未把资金状态作为盘中增强依据。",
            )
        ),
        summary_text=_without_summary_task_status(
            str(summary.get("summary_text", ""))
        ),
    )
    validate_local_fallback_report(report)
    return report


def validate_local_fallback_report(report: LocalFallbackReport) -> None:
    if report.report_mode != "local_fallback":
        raise ValueError("local fallback report has invalid report_mode")
    if not report.trade_date or not report.generated_at:
        raise ValueError("local fallback report requires trade_date and generated_at")
    if report.alert_count < 0 or report.scan_count < 0:
        raise ValueError("local fallback report counts cannot be negative")
    if not report.continuity.strip():
        raise ValueError("local fallback report requires continuity evidence")
    if any(not candidate.code or not candidate.name for candidate in report.top3):
        raise ValueError("local fallback Top3 contains an incomplete candidate")
    if len({candidate.code for candidate in report.top3}) != len(report.top3):
        raise ValueError("local fallback Top3 contains duplicate securities")
    if "15:30总结running" in report.continuity:
        raise ValueError("local fallback report contains a running summary state")
    if "15:30总结running" in report.summary_text:
        raise ValueError("local fallback report contains a running summary state")


def manifest_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_name(f"{pdf_path.name}.meta.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_pdf_manifest(
    pdf_path: Path,
    *,
    source_path: Path,
    report_mode: str,
    source_version: str,
    source_generated_at: str,
    source_commit_value: str | None = None,
) -> Path:
    manifest_path = manifest_path_for(pdf_path)
    manifest = {
        "manifest_version": PDF_MANIFEST_VERSION,
        "renderer_version": (
            LOCAL_FALLBACK_RENDERER_VERSION
            if report_mode == "local_fallback"
            else FULL_MARKET_RENDERER_VERSION
        ),
        "report_mode": report_mode,
        "source_version": source_version,
        "source_generated_at": source_generated_at,
        "source_sha256": sha256_file(source_path),
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_commit": source_commit_value or source_commit(),
    }
    temporary = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


def manifest_is_current(
    pdf_path: Path,
    *,
    source_path: Path,
    report_mode: str,
    source_version: str,
    source_generated_at: str,
    source_commit_value: str | None = None,
) -> bool:
    if not pdf_path.is_file() or not manifest_path_for(pdf_path).is_file():
        return False
    try:
        manifest = json.loads(manifest_path_for(pdf_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_renderer_version = (
        LOCAL_FALLBACK_RENDERER_VERSION
        if report_mode == "local_fallback"
        else FULL_MARKET_RENDERER_VERSION
    )
    expected = {
        "manifest_version": PDF_MANIFEST_VERSION,
        "renderer_version": expected_renderer_version,
        "report_mode": report_mode,
        "source_version": source_version,
        "source_generated_at": source_generated_at,
        "source_sha256": sha256_file(source_path),
        "source_commit": source_commit_value or source_commit(),
    }
    return all(manifest.get(key) == value for key, value in expected.items())


def write_local_fallback_artifacts(
    store: SQLiteStore,
    summary: Mapping[str, Any],
    *,
    reports_dir: Path,
    now: datetime,
    source_commit_value: str | None = None,
) -> LocalFallbackReport:
    """Write local JSON, Markdown, PDF and manifest as one report bundle."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = build_local_fallback_report(
        store,
        summary,
        now=now,
        source_commit_value=source_commit_value,
    )
    trade_date = report.trade_date
    json_path = reports_dir / f"{trade_date}-local-summary.json"
    md_path = reports_dir / f"{trade_date}-local-summary.md"
    pdf_path = reports_dir / f"{trade_date}-A股盘后回顾.pdf"
    source_record = dict(summary)
    source_record["summary_text"] = _without_summary_task_status(
        str(source_record.get("summary_text", ""))
    )
    source_record["health_summary"] = _without_summary_task_status(
        str(source_record.get("health_summary", ""))
    )
    source_record.update(
        {
            "report_mode": "local_fallback",
            "source_version": report.source_version,
            "source_generated_at": report.source_generated_at,
            "source_commit": report.source_commit,
            "local_fallback_report": report.as_record(),
        }
    )
    temporary_json = json_path.with_name(f".{json_path.name}.tmp")
    temporary_json.write_text(
        json.dumps(source_record, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary_json.replace(json_path)
    temporary_md = md_path.with_name(f".{md_path.name}.tmp")
    temporary_md.write_text(render_local_fallback_markdown(report), encoding="utf-8")
    temporary_md.replace(md_path)
    from .local_summary_pdf import render_local_fallback_pdf

    temporary_pdf = pdf_path.with_name(f".{pdf_path.name}.tmp")
    render_local_fallback_pdf(report, temporary_pdf)
    temporary_pdf.replace(pdf_path)
    write_pdf_manifest(
        pdf_path,
        source_path=json_path,
        report_mode="local_fallback",
        source_version=report.source_version,
        source_generated_at=report.source_generated_at,
        source_commit_value=report.source_commit,
    )
    return report


def render_local_fallback_markdown(report: LocalFallbackReport) -> str:
    """Render local SQLite facts without presenting a full market report."""
    lines = [
        f"# {report.trade_date} A股盘后本地运行总结",
        "",
        "> 盘后增强数据未取得；本报告只使用当天本地 SQLite、扫描、提醒和运行事件。",
        "",
        "## 运行连续性",
        "",
        f"- 扫描轮数：{report.scan_count}；HEALTHY：{report.healthy_scan_count}",
        f"- 覆盖率范围：{_coverage_text(report)}",
        f"- 运行会话：{report.runtime_session_count}；进程重启：{report.restart_count}",
        f"- 睡眠：{report.sleep_count}；唤醒：{report.wake_count}",
        f"- 连续性事实：{report.continuity}",
        "",
        "## 固定提醒与强异动",
        "",
    ]
    for alert in report.alerts:
        names = "、".join(alert.candidate_names) or "未记录候选"
        lines.append(
            f"- {_alert_markdown_label(alert.trigger_type)}："
            f"{_state_markdown_label(alert.state)}；{names}；"
            f"{alert.displayed_at or '未记录时间'}"
        )
    lines.extend(["", "## 尾盘稳定 Top3", ""])
    if report.top3:
        lines.append(f"来源：{report.top3_source or '未确认'}")
        for index, candidate in enumerate(report.top3, start=1):
            price = (
                f"¥{candidate.price:.2f}"
                if candidate.price is not None
                else "提醒/最后观察价格未记录"
            )
            change = (
                f"{candidate.change_pct:+.2f}%"
                if candidate.change_pct is not None
                else "涨跌未记录"
            )
            lines.append(
                f"{index}. {candidate.name}（{candidate.code}）— {price}，{change}，"
                f"板块：{candidate.sector or '板块未记录'}，等级：{candidate.level}"
            )
    else:
        lines.append("当日未形成可核验的稳定 Top3；没有使用 closing_performance 代替。")
    lines.extend(
        [
            "",
            "## 数据限制",
            "",
            f"- {report.market_limitation}",
            f"- 概念缓存：{report.concept_status}",
            f"- {report.fund_summary}",
            "",
            report.summary_text or "本日没有可展示的本地总结文字。",
            "",
            "本报告只用于内部只读观察，不构成投资建议，不连接交易账户。",
            "",
        ]
    )
    return "\n".join(lines)


def without_summary_task_status(value: str) -> str:
    return _without_summary_task_status(value)


def _coverage_text(report: LocalFallbackReport) -> str:
    if report.minimum_coverage is None or report.maximum_coverage is None:
        return "未取得"
    return f"{report.minimum_coverage:.1%}–{report.maximum_coverage:.1%}"


def _alert_markdown_label(value: str) -> str:
    labels = {
        "scheduled-09:45": "09:45固定提醒",
        "scheduled-14:45": "14:45固定提醒",
        "intraday": "盘中强异动",
    }
    return labels.get(value, value)


def _state_markdown_label(value: str) -> str:
    labels = {
        "succeeded": "成功",
        "failed": "失败",
        "planned": "待执行",
        "running": "执行中",
        "not_recorded": "未记录",
    }
    return labels.get(value, value)


def _build_alert_timeline(
    tasks: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> tuple[LocalFallbackAlert, ...]:
    output: list[LocalFallbackAlert] = []
    for trigger_type in ("scheduled-09:45", "scheduled-14:45"):
        task = next(
            (item for item in tasks if str(item.get("task_type")) == trigger_type),
            None,
        )
        event = _first_alert(history, trigger_type)
        candidates = _payload_candidates(event.get("payload_json")) if event else []
        output.append(
            LocalFallbackAlert(
                trigger_type=trigger_type,
                state=str(task.get("state", "not_recorded")) if task else "not_recorded",
                displayed_at=(str(event.get("displayed_at")) if event else None),
                candidate_names=tuple(
                    str(item.get("name", item.get("code", "")))
                    for item in candidates[:3]
                ),
                candidate_codes=tuple(
                    str(item.get("code", "")) for item in candidates[:3]
                ),
            )
        )
    for event in history:
        if not str(event.get("trigger_type", "")).startswith("intraday"):
            continue
        candidates = _payload_candidates(event.get("payload_json"))
        output.append(
            LocalFallbackAlert(
                trigger_type="intraday",
                state="succeeded",
                displayed_at=str(event.get("displayed_at", "")),
                candidate_names=tuple(
                    str(item.get("name", item.get("code", "")))
                    for item in candidates[:3]
                ),
                candidate_codes=tuple(
                    str(item.get("code", "")) for item in candidates[:3]
                ),
            )
        )
    return tuple(output)


def _candidates_from_payload(
    value: object,
    selection_source: str,
) -> tuple[LocalFallbackCandidate, ...]:
    return tuple(
        _candidate_from_mapping(item, selection_source=selection_source)
        for item in _payload_candidates(value)[:3]
    )


def _candidate_from_mapping(
    value: Mapping[str, Any],
    *,
    selection_source: str | None = None,
) -> LocalFallbackCandidate:
    reasons = value.get("reasons", ())
    reason_values = (
        tuple(str(item) for item in reasons if str(item).strip())
        if isinstance(reasons, (list, tuple))
        else ()
    )
    return LocalFallbackCandidate(
        code=str(value.get("code", value.get("ts_code", ""))),
        name=str(value.get("name", value.get("code", ""))),
        price=_first_number(value, "price", "alert_price", "last_price"),
        change_pct=_first_number(value, "change_pct", "pct_chg"),
        level=str(value.get("level", "观察")),
        sector=_optional_text(value.get("sector", value.get("sector_name"))),
        sector_type=_optional_text(value.get("sector_type")),
        score=_first_number(value, "total_score", "core_score", "score"),
        reasons=reason_values,
        source_ts=_optional_text(value.get("source_ts")),
        selection_source=selection_source or str(value.get("selection_source", "unknown")),
    )


def _alert_from_mapping(value: Mapping[str, Any]) -> LocalFallbackAlert:
    return LocalFallbackAlert(
        trigger_type=str(value.get("trigger_type", "")),
        state=str(value.get("state", "not_recorded")),
        displayed_at=_optional_text(value.get("displayed_at")),
        candidate_names=tuple(
            str(item) for item in _sequence(value.get("candidate_names"))
        ),
        candidate_codes=tuple(
            str(item) for item in _sequence(value.get("candidate_codes"))
        ),
    )


def _payload_candidates(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, Mapping):
        return []
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, Mapping)]


def _first_alert(
    history: list[dict[str, Any]], trigger_type: str
) -> dict[str, Any] | None:
    matches = [row for row in history if str(row.get("trigger_type")) == trigger_type]
    return matches[-1] if matches else None


def _continuity_text(
    summary: Mapping[str, Any],
    runs: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    sleep_count: int,
    wake_count: int,
) -> str:
    saved = _without_summary_task_status(str(summary.get("health_summary", "")))
    facts = [f"扫描轮数{len(runs)}轮"]
    if runs:
        healthy = sum(str(row.get("health")) == "HEALTHY" for row in runs)
        facts.append(f"成功扫描{healthy}轮")
    if sessions:
        facts.append(f"运行会话{len(sessions)}个")
    if sleep_count:
        facts.append(f"睡眠{sleep_count}次")
    if wake_count:
        facts.append(f"唤醒{wake_count}次")
    if saved:
        facts.append(saved)
    return "；".join(dict.fromkeys(facts))


def _without_summary_task_status(value: str) -> str:
    cleaned = re.sub(r"(?:^|；)15:30总结[^；。]*[；。]?", "；", value)
    cleaned = re.sub(r"；{2,}", "；", cleaned)
    return cleaned.strip("；。 ")


def _concept_status(value: str) -> str:
    match = re.search(r"概念缓存：([^；。]+)", value)
    return match.group(1).strip() if match else "未取得概念缓存状态"


def _is_before_three_pm(value: object, trade_date: str) -> bool:
    parsed = _parse_datetime(value)
    return (
        parsed is not None
        and parsed.date().isoformat() == trade_date
        and parsed.time() <= time(15, 0)
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sequence(value: object) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _first_number(value: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            return float(item)
    return None
