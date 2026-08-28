from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from stock_watcher.runtime.post_close_report_model import FULL_MARKET_RENDERER_VERSION

REPORT_RETENTION_DAYS = 31
REPORT_STEM_SUFFIX = "-A股盘后回顾"
# Human Owner accepted this visual contract on 2026-07-31. Any layout change
# requires an explicit decision and a version bump; daily exports only replace data.
POST_CLOSE_PDF_LAYOUT_VERSION = FULL_MARKET_RENDERER_VERSION
_FONT = "StockWatcherSans"
_FONT_MEDIUM = "StockWatcherSansMedium"
_INK = colors.HexColor("#172236")
_MUTED = colors.HexColor("#667085")
_LINE = colors.HexColor("#DCE3EB")
_SOFT = colors.HexColor("#F4F7FA")
_BLUE = colors.HexColor("#2457A6")
_RED = colors.HexColor("#C9413A")
_GREEN = colors.HexColor("#17814F")
_AMBER = colors.HexColor("#B7791F")
_CONTENT_WIDTH = A4[0] - (32 * mm)
_UP_SOFT = colors.HexColor("#FCEDEC")
_DOWN_SOFT = colors.HexColor("#EAF6F0")
_BLUE_SOFT = colors.HexColor("#EAF1FB")


class _MarketBreadthBar(Flowable):  # type: ignore[misc]
    """Small vector chart that keeps the market-width relationship legible."""

    def __init__(self, market: Mapping[str, Any]) -> None:
        super().__init__()
        self.market = market
        self.width = _CONTENT_WIDTH
        self.height = 27 * mm

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def draw(self) -> None:
        canvas = self.canv
        total = max(_number_value(self.market.get("securities")), 1.0)
        advances = max(_number_value(self.market.get("advances")), 0.0)
        declines = max(_number_value(self.market.get("declines")), 0.0)
        unchanged = max(_number_value(self.market.get("unchanged")), 0.0)
        parts = (
            ("上涨", advances, _RED, _UP_SOFT),
            ("下跌", declines, _GREEN, _DOWN_SOFT),
            ("平盘", unchanged, _MUTED, _SOFT),
        )
        pad = 12
        bar_x = pad
        bar_y = 20
        bar_width = self.width - (pad * 2)
        bar_height = 7
        canvas.setFillColor(_SOFT)
        canvas.roundRect(0, 0, self.width, self.height, 7, fill=1, stroke=0)
        canvas.setFillColor(_INK)
        canvas.setFont(_FONT_MEDIUM, 9.2)
        canvas.drawString(pad, self.height - 13, "市场宽度")
        cursor = bar_x
        for _label, count, colour, _soft in parts:
            segment_width = bar_width * count / total
            if segment_width <= 0:
                continue
            canvas.setFillColor(colour)
            canvas.roundRect(cursor, bar_y, segment_width, bar_height, 3, fill=1, stroke=0)
            cursor += segment_width
        canvas.setFont(_FONT, 7.8)
        for index, (label, count, colour, _soft) in enumerate(parts):
            ratio = count / total
            canvas.setFillColor(colour if label != "平盘" else _MUTED)
            label_x = pad + (bar_width / 3) * index
            canvas.drawString(
                label_x,
                7,
                f"{label} {_integer_text(count)}  {_percent_ratio(ratio)}",
            )


def validate_full_market_record(record: Mapping[str, Any]) -> None:
    """Reject local fallback records before they reach the full renderer."""
    if record.get("report_mode") == "local_fallback":
        raise ValueError("full_market PDF renderer cannot render local_fallback records")
    required = {
        "market",
        "market_segments",
        "top_sectors",
        "top3",
        "source_coverage",
        "data_limitations",
    }
    missing = sorted(key for key in required if key not in record)
    if missing:
        raise ValueError("full_market PDF record missing required fields: " + ", ".join(missing))
    market = record.get("market")
    top3 = record.get("top3")
    coverage = record.get("source_coverage")
    if not isinstance(market, Mapping) or not market.get("securities"):
        raise ValueError("full_market PDF record requires non-empty market statistics")
    if not isinstance(top3, Sequence) or isinstance(top3, (str, bytes)) or len(top3) != 3:
        raise ValueError("full_market PDF record requires exactly three top3 candidates")
    if not isinstance(coverage, Mapping) or not coverage:
        raise ValueError("full_market PDF record requires source coverage")
    if all(_is_placeholder(value) for value in market.values()):
        raise ValueError("full_market PDF record contains only placeholder market values")


def _is_placeholder(value: object) -> bool:
    return value in (None, "", "-", "未记录", "未分类")


def render_post_close_pdf(record: Mapping[str, Any], output_path: Path) -> Path:
    """Render one complete full-market post-close record as a fixed three-page PDF."""
    validate_full_market_record(record)
    _register_fonts()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_record(record)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{normalized['trade_date']} A股盘后回顾",
        author="StockWatcher",
        subject="只读A股盘后回顾",
        creator="StockWatcher deterministic post-close renderer",
        pageCompression=1,
    )
    styles = _styles()
    story: list[Any] = []
    story.extend(_page_one(normalized, styles))
    story.append(PageBreak())
    story.extend(_page_two(normalized, styles))
    story.append(PageBreak())
    story.extend(_page_three(normalized, styles))
    document.build(story, onFirstPage=_decorate_page, onLaterPages=_decorate_page)
    return output_path


def prune_post_close_reports(
    reports_dir: Path,
    *,
    reference_date: date,
    retention_days: int = REPORT_RETENTION_DAYS,
) -> tuple[Path, ...]:
    """Delete only recognized internal report artifacts older than the retention window."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least one")
    if not reports_dir.is_dir():
        return ()
    cutoff = reference_date - timedelta(days=retention_days - 1)
    removed: list[Path] = []
    for path in reports_dir.iterdir():
        if not path.is_file() or path.suffix.casefold() not in {".json", ".md", ".pdf"}:
            continue
        parsed = _date_from_report_name(path.name)
        if parsed is None or parsed >= cutoff:
            continue
        path.unlink()
        removed.append(path)
    return tuple(sorted(removed))


def list_post_close_report_dates(
    reports_dir: Path,
    *,
    reference_date: date,
    retention_days: int = REPORT_RETENTION_DAYS,
) -> tuple[str, ...]:
    if retention_days < 1:
        raise ValueError("retention_days must be at least one")
    cutoff = reference_date - timedelta(days=retention_days - 1)
    if not reports_dir.is_dir():
        return ()
    dates = {
        parsed.isoformat()
        for path in reports_dir.iterdir()
        if path.is_file()
        and path.suffix.casefold() in {".json", ".md", ".pdf"}
        and (parsed := _date_from_report_name(path.name)) is not None
        and cutoff <= parsed <= reference_date
    }
    return tuple(sorted(dates, reverse=True))


def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    market = _mapping(record.get("market"))
    top3 = _sequence_of_mappings(record.get("top3"))
    sectors = _normalize_sectors(record.get("top_sectors"))
    trade_date = str(record.get("trade_date", ""))[:10]
    if not trade_date:
        raise ValueError("post-close record has no trade_date")
    generated_at = str(record.get("generated_at", ""))
    retrospective = str(record.get("verdict", "")).casefold() == "retrospective_only" or any(
        bool(candidate.get("retrospective_only")) for candidate in top3
    )
    if market:
        tone = _market_tone(_float(market.get("up_ratio")), _float(market.get("median_change_pct")))
    else:
        tone = _tone_from_summary(str(record.get("summary_text", "")))
    return {
        "trade_date": trade_date,
        "generated_at": generated_at,
        "retrospective": retrospective,
        "tone": tone,
        "market": market,
        "segments": _sequence_of_mappings(record.get("market_segments"))[:5],
        "sectors": sectors[:10],
        "top3": top3[:3],
        "fund_summary": str(record.get("fund_summary", "资金未确认，本次排序未使用资金项。")),
        "health_summary": str(record.get("health_summary", "")),
        "summary_text": str(record.get("summary_text", "")),
        "limitations": _string_sequence(record.get("data_limitations"))[:6],
        "coverage": _mapping(record.get("source_coverage")),
        "alert_count": int(record.get("intraday_alert_count", record.get("alert_count", 0)) or 0),
        "interruptions": int(record.get("health_interruption_count", 0) or 0),
        "timeline": _sequence_of_mappings(record.get("alert_timeline"))[:8],
    }


def _page_one(data: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> list[Any]:
    market = _mapping(data["market"])
    elements = _page_heading(
        str(data["trade_date"]),
        "市场全景",
        _evidence_label(bool(data["retrospective"])),
        styles,
    )
    elements.extend(
        [
            _market_hero(data, market, styles),
            Spacer(1, 4 * mm),
            _MarketBreadthBar(market),
            Spacer(1, 4 * mm),
            _metric_grid(market, styles),
            Spacer(1, 5 * mm),
            Paragraph("主要市场分段", styles["section"]),
            Spacer(1, 2 * mm),
            _segments_table(_sequence_of_mappings(data["segments"]), styles),
            Spacer(1, 5 * mm),
            Paragraph("强势行业", styles["section"]),
            Spacer(1, 2 * mm),
            _sectors_table(_sequence_of_mappings(data["sectors"])[:5], styles),
        ]
    )
    return elements


def _page_two(data: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> list[Any]:
    top3 = _sequence_of_mappings(data["top3"])
    elements = _page_heading(
        str(data["trade_date"]),
        "收盘观察 Top 3",
        "固定三只｜同板块最多两只｜资金缺失不阻塞",
        styles,
    )
    elements.extend(
        [
            _top3_summary(top3, styles),
            Spacer(1, 4 * mm),
        ]
    )
    if not top3:
        elements.append(Paragraph("本日没有形成可核对的收盘观察Top 3。", styles["body"]))
        return elements
    sector_counts = Counter(str(item.get("sector", "未分类")) for item in top3)
    for index, candidate in enumerate(top3, start=1):
        elements.append(_candidate_card(candidate, index, styles))
        elements.append(Spacer(1, 3.5 * mm))
    allocation = "；".join(f"{sector} {count}只" for sector, count in sector_counts.items())
    elements.extend(
        [
            Spacer(1, 1 * mm),
            Paragraph(
                f"板块分布：{escape(allocation)}。同板块最多两只规则"
                f"{'已满足' if max(sector_counts.values(), default=0) <= 2 else '未满足'}。",
                styles["note"],
            ),
        ]
    )
    return elements


def _page_three(data: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> list[Any]:
    summary = _closing_summary(data)
    elements = _page_heading(
        str(data["trade_date"]),
        "运行回顾与数据说明",
        _generation_badge(bool(data["retrospective"])),
        styles,
    )
    elements.extend(
        [
            _summary_panel(summary, styles),
            Spacer(1, 4 * mm),
            _run_stats(data, styles),
            Spacer(1, 5 * mm),
            Paragraph("当日提醒记录", styles["section"]),
            Spacer(1, 2 * mm),
            _timeline_table(data, styles),
            Spacer(1, 5 * mm),
            Paragraph("资金与数据健康", styles["section"]),
            Spacer(1, 2 * mm),
            _status_box("资金状态", str(data["fund_summary"]), _AMBER, styles),
            Spacer(1, 2.5 * mm),
            _status_box(
                "数据状态",
                _health_copy(data),
                _BLUE if int(data["interruptions"]) == 0 else _AMBER,
                styles,
            ),
            Spacer(1, 5 * mm),
            Paragraph("数据限制", styles["section"]),
            Spacer(1, 2 * mm),
        ]
    )
    limitations = list(_string_sequence(data["limitations"]))
    if bool(data["retrospective"]):
        limitations.insert(
            0,
            "本报告为真实静态收盘数据回顾，不是盘中实时Top 3、固定时间Live证据或Windows验收。",
        )
    if not limitations:
        limitations = ["未记录额外数据限制；仍应结合当日数据状态核对。"]
    elements.append(_limitations_table(limitations[:6], styles))
    elements.extend(
        [
            Spacer(1, 5 * mm),
            HRFlowable(width="100%", color=_LINE, thickness=0.8),
            Spacer(1, 4 * mm),
            Paragraph(
                "生成方式：程序使用固定规则计算市场宽度、板块强度和收盘观察Top 3，"
                "再由本地PDF模板排版；未调用AI大模型，也未向外部服务上传候选、行情或凭据。",
                styles["note"],
            ),
            Spacer(1, 3 * mm),
            Paragraph(
                "本报告仅用于只读观察与盘后复盘，不构成投资建议，不连接交易账户，"
                "不读取持仓，也不执行任何交易。",
                styles["disclaimer"],
            ),
        ]
    )
    return elements


def _page_heading(
    trade_date: str,
    page_title: str,
    badge: str,
    styles: Mapping[str, ParagraphStyle],
) -> list[Any]:
    return [
        Table(
            [
                [
                    Paragraph("STOCKWATCHER  /  A股观察简报", styles["brand"]),
                    Paragraph(escape(trade_date), styles["date"]),
                ]
            ],
            colWidths=[140 * mm, 38 * mm],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                ]
            ),
        ),
        Spacer(1, 6 * mm),
        Paragraph(f"{escape(trade_date)}  ·  收盘复盘", styles["kicker"]),
        Spacer(1, 1.5 * mm),
        Paragraph(escape(page_title), styles["title"]),
        Spacer(1, 3 * mm),
        Table(
            [[Paragraph(escape(badge), styles["badge"])]],
            colWidths=[_CONTENT_WIDTH],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _SOFT),
                    ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 9),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        ),
        Spacer(1, 5 * mm),
    ]


def _market_hero(
    data: Mapping[str, Any],
    market: Mapping[str, Any],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    tone = str(data["tone"])
    tone_colour = _tone_color(tone)
    up_ratio = _percent_ratio(market.get("up_ratio"))
    median = _percent_value(market.get("median_change_pct"))
    copy = (
        f"全市场 {_integer_text(market.get('securities'))} 只股票，"
        f"上涨比例 {up_ratio}，涨跌中位数 {median}。"
    )
    return Table(
        [
            [
                Paragraph(
                    "<font color='#8BA9D5'>今日市场判断</font><br/>"
                    f"<font color='{tone_colour}'><b>{escape(tone)}</b></font>",
                    styles["hero_tone"],
                ),
                Paragraph(escape(copy), styles["hero_copy"]),
            ]
        ],
        colWidths=[56 * mm, _CONTENT_WIDTH - (56 * mm)],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _INK),
                ("LINEBEFORE", (0, 0), (0, 0), 4, tone_colour),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )


def _top3_summary(
    top3: Sequence[Mapping[str, Any]],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    sectors = {str(item.get("sector", "未分类")) for item in top3}
    same_sector_ok = (
        max(
            Counter(str(item.get("sector", "未分类")) for item in top3).values(),
            default=0,
        )
        <= 2
    )
    values = (
        ("收盘观察", f"{len(top3)} 只"),
        ("覆盖行业", f"{len(sectors)} 个"),
        ("同板块上限", "已满足" if same_sector_ok else "需核对"),
    )
    cells = [
        Paragraph(
            f"<font color='#667085'>{escape(label)}</font><br/><b>{escape(value)}</b>",
            styles["summary_stat"],
        )
        for label, value in values
    ]
    return Table(
        [cells],
        colWidths=[_CONTENT_WIDTH / 3] * 3,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BLUE_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D8EE")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D8EE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        ),
    )


def _summary_panel(summary: str, styles: Mapping[str, ParagraphStyle]) -> Table:
    return Table(
        [
            [
                Paragraph("收盘结论", styles["panel_label"]),
                Paragraph(escape(summary), styles["panel_copy"]),
            ]
        ],
        colWidths=[29 * mm, _CONTENT_WIDTH - (29 * mm)],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _BLUE_SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#C8D8EE")),
                ("LINEBEFORE", (0, 0), (0, 0), 3, _BLUE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )


def _run_stats(data: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> Table:
    values = (
        ("自动提醒", f"{int(data['alert_count'])} 次"),
        ("数据中断", f"{int(data['interruptions'])} 次"),
        ("报告口径", "静态收盘回溯" if data["retrospective"] else "当日收盘数据"),
    )
    cells = [
        Paragraph(
            f"<font color='#667085'>{escape(label)}</font><br/>"
            f"<font color='#172236'><b>{escape(value)}</b></font>",
            styles["summary_stat"],
        )
        for label, value in values
    ]
    return Table(
        [cells],
        colWidths=[_CONTENT_WIDTH / 3] * 3,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SOFT),
                ("BOX", (0, 0), (-1, -1), 0.5, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        ),
    )


def _limitations_table(
    limitations: Sequence[str],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    rows: list[list[Any]] = []
    for index, item in enumerate(limitations, start=1):
        rows.append(
            [
                Paragraph(f"{index:02d}", styles["limit_number"]),
                Paragraph(escape(item), styles["limit_copy"]),
            ]
        )
    return Table(
        rows
        or [
            [
                Paragraph("--", styles["limit_number"]),
                Paragraph("未记录额外限制", styles["limit_copy"]),
            ]
        ],
        colWidths=[12 * mm, _CONTENT_WIDTH - (12 * mm)],
        style=TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        ),
    )


def _closing_summary(data: Mapping[str, Any]) -> str:
    existing = str(data.get("summary_text", "")).strip()
    if existing:
        return existing
    market = _mapping(data["market"])
    sector_names = [
        str(item.get("name", "未分类")) for item in _sequence_of_mappings(data["sectors"])[:3]
    ]
    sectors = "、".join(sector_names) if sector_names else "强势行业"
    return (
        f"市场{data['tone']}：上涨比例 {_percent_ratio(market.get('up_ratio'))}，"
        f"涨跌中位数 {_percent_value(market.get('median_change_pct'))}；"
        f"强势集中在{sectors}，收盘观察 Top 3 均通过板块硬门。"
    )


def _metric_grid(market: Mapping[str, Any], styles: Mapping[str, ParagraphStyle]) -> Table:
    metrics = [
        ("统计股票", _integer_text(market.get("securities"), "只")),
        ("涨跌中位数", _percent_value(market.get("median_change_pct"))),
        ("明显走强", _integer_text(market.get("strong_count"), "只")),
        ("成交额", _amount_text(market.get("amount_cny"))),
    ]
    cards = [
        Paragraph(
            f"<font color='#667085'>{escape(label)}</font><br/>"
            f"<font color='#172236'><b>{escape(value)}</b></font>",
            styles["metric_card"],
        )
        for label, value in metrics
    ]
    return Table(
        [cards],
        colWidths=[_CONTENT_WIDTH / 4] * 4,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )


def _segments_table(
    segments: Sequence[Mapping[str, Any]],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    rows: list[list[Any]] = [
        [
            Paragraph(value, styles["table_head"])
            for value in ("市场", "股票数", "上涨比例", "涨跌中位数")
        ]
    ]
    if segments:
        rows.extend(
            [
                Paragraph(escape(str(item.get("market", "未分类"))), styles["table"]),
                Paragraph(_integer_text(item.get("securities")), styles["table"]),
                Paragraph(_percent_ratio(item.get("up_ratio")), styles["table"]),
                Paragraph(_percent_value(item.get("median_change_pct")), styles["table"]),
            ]
            for item in segments[:5]
        )
    else:
        rows.append(
            [Paragraph("暂无分段数据", styles["table"])] + [Paragraph("-", styles["table"])] * 3
        )
    return _standard_table(rows, [54 * mm, 38 * mm, 43 * mm, 43 * mm])


def _sectors_table(
    sectors: Sequence[Mapping[str, Any]],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    rows: list[list[Any]] = [
        [
            Paragraph(value, styles["table_head"])
            for value in ("行业", "成分数", "上涨比例", "明显走强", "中位涨幅")
        ]
    ]
    if sectors:
        rows.extend(
            [
                Paragraph(escape(str(item.get("name", "未分类"))), styles["table"]),
                Paragraph(_integer_text(item.get("member_count")), styles["table"]),
                Paragraph(_percent_ratio(item.get("up_ratio")), styles["table"]),
                Paragraph(_integer_text(item.get("strong_count")), styles["table"]),
                Paragraph(_percent_value(item.get("median_change_pct")), styles["table"]),
            ]
            for item in sectors[:5]
        )
    else:
        rows.append(
            [Paragraph("无满足板块硬门的行业", styles["table"])]
            + [Paragraph("-", styles["table"])] * 4
        )
    return _standard_table(rows, [48 * mm, 29 * mm, 35 * mm, 34 * mm, 32 * mm])


def _candidate_card(
    candidate: Mapping[str, Any],
    fallback_rank: int,
    styles: Mapping[str, ParagraphStyle],
) -> KeepTogether:
    rank = int(candidate.get("rank", fallback_rank) or fallback_rank)
    name = str(candidate.get("name", candidate.get("code", "未命名")))
    code = str(candidate.get("code", ""))
    level = str(candidate.get("level", "观察"))
    close = _number_text(candidate.get("close", candidate.get("close_price")), 2)
    change = _percent_value(candidate.get("change_pct"))
    sector = str(candidate.get("sector", "未分类"))
    sector_ratio = _percent_ratio(candidate.get("sector_up_ratio"))
    strong_count = _integer_text(candidate.get("sector_strong_count"))
    relative = _percent_value(candidate.get("sector_relative_strength"))
    amount_ratio = _ratio_text(candidate.get("amount_ratio_3d"))
    trend = _percent_value(candidate.get("trend_3d_pct"))
    reasons = _string_sequence(candidate.get("reasons"))[:3]
    facts = (
        ("收盘价", f"¥{close}"),
        ("所属行业", sector),
        ("行业上涨", sector_ratio),
        ("相对行业", relative),
        ("三日成交额比", amount_ratio),
        ("三日趋势", trend),
        ("行业走强", f"{strong_count}只"),
        ("综合评分", _number_text(candidate.get("core_score"), 1)),
    )
    fact_rows: list[list[Any]] = []
    for offset in range(0, len(facts), 4):
        fact_rows.append(
            [
                Paragraph(
                    f"<font color='#667085'>{escape(label)}</font><br/><b>{escape(value)}</b>",
                    styles["fact_pair"],
                )
                for label, value in facts[offset : offset + 4]
            ]
        )
    hard_gate = _hard_gate_copy(candidate)
    reasons_copy = "；".join(reasons) if reasons else "本日收盘数据形成的确定性观察候选"
    card = Table(
        [
            [
                Paragraph(f"{rank:02d}", styles["rank"]),
                Paragraph(
                    f"<b>{escape(name)}</b><br/>"
                    f"<font color='#667085'>{escape(code)}  ·  {escape(sector)}</font>",
                    styles["candidate_title"],
                ),
                Paragraph(
                    f"<font color='#C9413A'><b>{escape(change)}</b></font><br/>"
                    f"<font color='#667085'>{escape(level)}  |  收盘</font>",
                    styles["change"],
                ),
            ],
            [
                Table(
                    fact_rows,
                    colWidths=[(_CONTENT_WIDTH - 10 * mm) / 4] * 4,
                    style=TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("LINEBELOW", (0, 0), (-1, -2), 0.35, _LINE),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    ),
                ),
                "",
                "",
            ],
            [
                Paragraph(
                    f"<font color='#2457A6'><b>板块硬门</b></font>  {escape(hard_gate)}"
                    f"<br/><font color='#667085'>依据：{escape(reasons_copy)}</font>",
                    styles["candidate_reason"],
                ),
                "",
                "",
            ],
        ],
        colWidths=[14 * mm, 116 * mm, 48 * mm],
        style=TableStyle(
            [
                ("SPAN", (1, 1), (2, 1)),
                ("SPAN", (0, 2), (2, 2)),
                ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
                ("BACKGROUND", (0, 0), (0, 0), _INK),
                ("BOX", (0, 0), (-1, -1), 0.8, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, 0), "CENTER"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        ),
    )
    return KeepTogether([card])


def _timeline_table(
    data: Mapping[str, Any],
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    timeline = _sequence_of_mappings(data["timeline"])
    grouped = {str(item.get("trigger_type", "")): item for item in timeline}
    rows: list[list[Any]] = [
        [
            Paragraph("时点", styles["table_head"]),
            Paragraph("状态", styles["table_head"]),
            Paragraph("观察股票", styles["table_head"]),
        ]
    ]
    for trigger, label in (
        ("scheduled-09:45", "09:45固定提醒"),
        ("scheduled-14:45", "14:45固定提醒"),
    ):
        item = grouped.get(trigger)
        rows.append(
            [
                Paragraph(label, styles["table"]),
                Paragraph("已记录" if item else "未记录", styles["table"]),
                Paragraph(
                    escape(_timeline_names(item) if item else "未观察到，不补写Live证据"),
                    styles["table"],
                ),
            ]
        )
    intraday = [item for item in timeline if item.get("trigger_type") == "intraday"]
    rows.append(
        [
            Paragraph("盘中强异动", styles["table"]),
            Paragraph(f"{len(intraday)}次", styles["table"]),
            Paragraph(
                escape(
                    "；".join(_timeline_names(item) for item in intraday[:2])
                    or "未观察到自然强异动"
                ),
                styles["table"],
            ),
        ]
    )
    rows.append(
        [
            Paragraph("自动提醒合计", styles["table"]),
            Paragraph(f"{int(data['alert_count'])}次", styles["table"]),
            Paragraph("手动查看不计入提醒限额", styles["table"]),
        ]
    )
    return _standard_table(rows, [46 * mm, 34 * mm, 98 * mm])


def _status_box(
    title: str,
    copy: str,
    accent: colors.Color,
    styles: Mapping[str, ParagraphStyle],
) -> Table:
    return Table(
        [
            [
                Paragraph(escape(title), styles["status_title"]),
                Paragraph(escape(copy or "未记录"), styles["body"]),
            ]
        ],
        colWidths=[32 * mm, _CONTENT_WIDTH - (32 * mm)],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SOFT),
                ("BOX", (0, 0), (-1, -1), 0.7, _LINE),
                ("LINEBEFORE", (0, 0), (0, 0), 3, accent),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )


def _standard_table(rows: Sequence[Sequence[Any]], widths: Sequence[float]) -> Table:
    return Table(
        rows,
        colWidths=list(widths),
        repeatRows=1,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
            ]
        ),
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "base",
        fontName=_FONT,
        fontSize=9.2,
        leading=14,
        textColor=_INK,
        alignment=TA_LEFT,
    )
    return {
        "brand": ParagraphStyle(
            "brand",
            parent=base,
            fontSize=9,
            leading=11,
            textColor=_BLUE,
            fontName=_FONT_MEDIUM,
            spaceAfter=0,
        ),
        "date": ParagraphStyle(
            "date",
            parent=base,
            fontSize=9,
            leading=11,
            textColor=_MUTED,
            alignment=2,
        ),
        "kicker": ParagraphStyle(
            "kicker",
            parent=base,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            fontName=_FONT_MEDIUM,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base,
            fontSize=22,
            leading=27,
            textColor=_INK,
            fontName=_FONT_MEDIUM,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base,
            fontSize=12,
            leading=16,
            textColor=_BLUE,
        ),
        "badge": ParagraphStyle(
            "badge",
            parent=base,
            fontSize=8.5,
            leading=12,
            textColor=_MUTED,
        ),
        "lead": ParagraphStyle(
            "lead",
            parent=base,
            fontSize=11,
            leading=16,
        ),
        "section": ParagraphStyle(
            "section",
            parent=base,
            fontSize=12.5,
            leading=17,
            textColor=_INK,
            fontName=_FONT_MEDIUM,
        ),
        "body": base,
        "note": ParagraphStyle(
            "note",
            parent=base,
            fontSize=8.5,
            leading=13,
            textColor=_MUTED,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer",
            parent=base,
            fontSize=8.5,
            leading=13,
            textColor=_MUTED,
            alignment=TA_CENTER,
        ),
        "metric_label": ParagraphStyle(
            "metric_label",
            parent=base,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
        ),
        "metric_value": ParagraphStyle(
            "metric_value",
            parent=base,
            fontSize=13,
            leading=17,
            textColor=_INK,
        ),
        "metric_card": ParagraphStyle(
            "metric_card",
            parent=base,
            fontSize=9,
            leading=14,
        ),
        "hero_tone": ParagraphStyle(
            "hero_tone",
            parent=base,
            fontSize=10,
            leading=16,
            textColor=colors.white,
            fontName=_FONT_MEDIUM,
        ),
        "hero_copy": ParagraphStyle(
            "hero_copy",
            parent=base,
            fontSize=9.1,
            leading=14,
            textColor=colors.HexColor("#E7EEF8"),
        ),
        "summary_stat": ParagraphStyle(
            "summary_stat",
            parent=base,
            fontSize=9,
            leading=14,
        ),
        "panel_label": ParagraphStyle(
            "panel_label",
            parent=base,
            fontSize=9.5,
            leading=14,
            fontName=_FONT_MEDIUM,
        ),
        "panel_copy": ParagraphStyle(
            "panel_copy",
            parent=base,
            fontSize=9.2,
            leading=14,
        ),
        "table_head": ParagraphStyle(
            "table_head",
            parent=base,
            fontSize=8.2,
            leading=11,
            textColor=colors.white,
            fontName=_FONT_MEDIUM,
        ),
        "table": ParagraphStyle(
            "table",
            parent=base,
            fontSize=8.2,
            leading=11,
        ),
        "candidate_title": ParagraphStyle(
            "candidate_title",
            parent=base,
            fontSize=12.5,
            leading=16,
        ),
        "rank": ParagraphStyle(
            "rank",
            parent=base,
            fontSize=15,
            leading=18,
            textColor=colors.white,
            alignment=TA_CENTER,
            fontName=_FONT_MEDIUM,
        ),
        "change": ParagraphStyle(
            "change",
            parent=base,
            fontSize=10,
            leading=14,
            alignment=2,
        ),
        "fact_label": ParagraphStyle(
            "fact_label",
            parent=base,
            fontSize=7.7,
            leading=10,
            textColor=_MUTED,
        ),
        "fact_value": ParagraphStyle(
            "fact_value",
            parent=base,
            fontSize=8.4,
            leading=11,
        ),
        "fact_pair": ParagraphStyle(
            "fact_pair",
            parent=base,
            fontSize=8.3,
            leading=12,
        ),
        "candidate_reason": ParagraphStyle(
            "candidate_reason",
            parent=base,
            fontSize=8.1,
            leading=12,
        ),
        "status_title": ParagraphStyle(
            "status_title",
            parent=base,
            fontSize=9,
            leading=13,
            textColor=_INK,
            fontName=_FONT_MEDIUM,
        ),
        "limit_number": ParagraphStyle(
            "limit_number",
            parent=base,
            fontSize=8,
            leading=12,
            textColor=_BLUE,
            fontName=_FONT_MEDIUM,
        ),
        "limit_copy": ParagraphStyle(
            "limit_copy",
            parent=base,
            fontSize=8.1,
            leading=12,
            textColor=_MUTED,
        ),
    }


def registered_font_names() -> tuple[str, str]:
    """Return the font names actually registered for PDF rendering."""
    _register_fonts()
    return (_FONT, _FONT_MEDIUM)


def _register_fonts() -> None:
    global _FONT, _FONT_MEDIUM
    if _FONT in pdfmetrics.getRegisteredFontNames():
        return
    candidates = (
        (
            Path("/System/Library/Fonts/STHeiti Light.ttc"),
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
            1,
            0,
        ),
        (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            0,
            0,
        ),
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            2,
            2,
        ),
    )
    for regular_path, medium_path, regular_index, medium_index in candidates:
        if not regular_path.is_file():
            continue
        try:
            pdfmetrics.registerFont(
                TTFont(
                    _FONT,
                    str(regular_path),
                    subfontIndex=regular_index,
                )
            )
            pdfmetrics.registerFont(
                TTFont(
                    _FONT_MEDIUM,
                    str(medium_path if medium_path.is_file() else regular_path),
                    subfontIndex=medium_index if medium_path.is_file() else regular_index,
                )
            )
            return
        except Exception:
            continue
    _FONT = "STSong-Light"
    _FONT_MEDIUM = _FONT
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))


def _decorate_page(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setStrokeColor(_LINE)
    canvas.setLineWidth(0.5)
    canvas.line(16 * mm, 13 * mm, A4[0] - 16 * mm, 13 * mm)
    canvas.setFillColor(_MUTED)
    canvas.setFont(_FONT, 7.5)
    canvas.drawString(16 * mm, 8.5 * mm, "StockWatcher | 只读盘后观察")
    canvas.drawRightString(
        A4[0] - 16 * mm,
        8.5 * mm,
        f"{canvas.getPageNumber()} / 3",
    )
    canvas.restoreState()


def _normalize_sectors(value: object) -> tuple[Mapping[str, Any], ...]:
    output: list[Mapping[str, Any]] = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    for item in value:
        if isinstance(item, Mapping):
            output.append(item)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) >= 2:
            output.append({"name": item[0], "strong_count": item[1]})
    return tuple(output)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _number_value(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _market_tone(up_ratio: float, median_change_pct: float) -> str:
    if up_ratio >= 0.55 and median_change_pct > 0:
        return "整体偏强"
    if up_ratio <= 0.45 and median_change_pct < 0:
        return "整体偏弱"
    return "整体分化"


def _tone_from_summary(value: str) -> str:
    for tone in ("整体偏强", "整体偏弱", "整体分化"):
        if tone in value:
            return tone
    return "数据待核对"


def _tone_color(tone: str) -> str:
    if "强" in tone:
        return "#C9413A"
    if "弱" in tone:
        return "#17814F"
    return "#2457A6"


def _evidence_label(retrospective: bool) -> str:
    if retrospective:
        return "真实静态收盘数据｜高级诊断兜底｜非盘中Live"
    return "15:30自动生成｜真实收盘数据｜只读观察"


def _generation_badge(retrospective: bool) -> str:
    if retrospective:
        return "盘后补生成｜最近31个自然日保留｜本地确定性脚本"
    return "15:30自动生成｜最近31个自然日保留｜本地确定性脚本"


def _integer_text(value: object, suffix: str = "") -> str:
    if isinstance(value, (int, float)):
        return f"{int(value):,}{suffix}"
    return "-"


def _percent_ratio(value: object) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "-"


def _percent_value(value: object) -> str:
    return f"{float(value):+.2f}%" if isinstance(value, (int, float)) else "-"


def _number_text(value: object, digits: int) -> str:
    return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "-"


def _ratio_text(value: object) -> str:
    return f"{float(value):.2f}倍" if isinstance(value, (int, float)) else "-"


def _amount_text(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    amount = float(value)
    if abs(amount) >= 1_000_000_000_000:
        return f"{amount / 1_000_000_000_000:.2f}万亿"
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.0f}亿元"
    return f"{amount:,.0f}元"


def _hard_gate_copy(candidate: Mapping[str, Any]) -> str:
    ratio = candidate.get("sector_up_ratio")
    strong = candidate.get("sector_strong_count")
    if isinstance(ratio, (int, float)) and isinstance(strong, (int, float)):
        passed = float(ratio) > 0.5 and int(strong) >= 3
        return (
            f"行业上涨比例{float(ratio):.1%}，明显走强{int(strong)}只，"
            f"{'通过' if passed else '未通过'}收盘回顾硬门"
        )
    return "本版未保存完整板块硬门字段，需结合原始报告核对"


def _timeline_names(item: Mapping[str, Any] | None) -> str:
    if item is None:
        return ""
    names = item.get("candidate_names")
    if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
        return "、".join(str(value) for value in names if str(value))
    return str(item.get("summary", "已记录"))


def _health_copy(data: Mapping[str, Any]) -> str:
    saved = str(data.get("health_summary", "")).strip()
    if saved:
        return saved
    coverage = _mapping(data.get("coverage"))
    stock_records = _integer_text(coverage.get("stock_records"))
    daily_records = _mapping(coverage.get("daily_records"))
    daily_count = max(
        (int(value) for value in daily_records.values() if isinstance(value, (int, float))),
        default=0,
    )
    return (
        f"股票资料{stock_records}条，最大单日日线覆盖{daily_count:,}条；"
        f"记录到{int(data.get('interruptions', 0))}次数据延迟或中断。"
    )


def _date_from_report_name(filename: str) -> date | None:
    suffixes = tuple(f"{REPORT_STEM_SUFFIX}{ext}" for ext in (".json", ".md", ".pdf"))
    if not filename.endswith(suffixes):
        return None
    try:
        return datetime.strptime(filename[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
