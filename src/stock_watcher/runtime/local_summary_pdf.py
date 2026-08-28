from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .post_close_pdf import registered_font_names
from .post_close_report_model import (
    LOCAL_FALLBACK_RENDERER_VERSION,
    LocalFallbackReport,
    validate_local_fallback_report,
)

_CONTENT_WIDTH = A4[0] - 32 * mm
_INK = colors.HexColor("#172236")
_MUTED = colors.HexColor("#667085")
_LINE = colors.HexColor("#DCE3EB")
_SOFT = colors.HexColor("#F4F7FA")
_BLUE = colors.HexColor("#2457A6")
_AMBER = colors.HexColor("#B7791F")


def render_local_fallback_pdf(report: LocalFallbackReport, output_path: Path) -> Path:
    """Render the local-only report without pretending it is a full market review."""
    validate_local_fallback_report(report)
    registered_font_names()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"{report.trade_date} A股盘后本地运行总结",
        author="StockWatcher",
        subject="本地运行连续性总结",
        creator=f"StockWatcher {LOCAL_FALLBACK_RENDERER_VERSION}",
        pageCompression=1,
    )
    styles = _styles()
    first_page: list[Any] = [
        _heading(report, styles),
        _notice(report.market_limitation, styles),
        Spacer(1, 4 * mm),
        Paragraph("运行连续性", styles["section"]),
        Spacer(1, 1 * mm),
        _continuity_table(report, styles),
        Spacer(1, 4 * mm),
        Paragraph("固定提醒与强异动", styles["section"]),
        Spacer(1, 1 * mm),
        _alerts_table(report.alerts, styles),
    ]
    second_page: list[Any] = [
        Paragraph("STOCKWATCHER / 本地运行证据", styles["brand"]),
        Paragraph(report.trade_date, styles["date"]),
        Spacer(1, 3 * mm),
        Paragraph("尾盘稳定 Top3", styles["title"]),
        Paragraph(
            f"本地兜底 · source {report.source_version} · "
            f"commit {report.source_commit[:12]}",
            styles["badge"],
        ),
        Spacer(1, 4 * mm),
        _top3_table(report, styles),
        Spacer(1, 5 * mm),
        _status_box("概念缓存", report.concept_status, _BLUE, styles),
        Spacer(1, 2 * mm),
        _status_box("资金状态", report.fund_summary, _AMBER, styles),
        Spacer(1, 4 * mm),
        Paragraph("本地总结", styles["section"]),
        Spacer(1, 1 * mm),
        Paragraph(report.summary_text or "本日没有可展示的本地总结文字。", styles["body"]),
        Spacer(1, 4 * mm),
        Paragraph(
            "本报告仅用于内部只读观察，不构成投资建议，不连接交易账户。",
            styles["body"],
        ),
    ]
    document.build(
        [*first_page, PageBreak(), *second_page],
        onFirstPage=_decorate_page,
        onLaterPages=_decorate_page,
    )
    return output_path


def _heading(report: LocalFallbackReport, styles: dict[str, ParagraphStyle]) -> Table:
    return Table(
        [
            [
                Paragraph("STOCKWATCHER / 本地运行证据", styles["brand"]),
                Paragraph(report.trade_date, styles["date"]),
            ],
            [
                Paragraph(
                    "A股盘后本地运行总结（盘后增强数据未取得）",
                    styles["title"],
                ),
                "",
            ],
            [
                Paragraph(
                    f"15:30 本地兜底 · source {report.source_version} · "
                    f"commit {report.source_commit[:12]}",
                    styles["badge"],
                ),
                "",
            ],
        ],
        colWidths=[_CONTENT_WIDTH - 38 * mm, 38 * mm],
        style=TableStyle(
            [
                ("SPAN", (0, 1), (-1, 1)),
                ("SPAN", (0, 2), (-1, 2)),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        ),
    )


def _notice(value: str, styles: dict[str, ParagraphStyle]) -> Table:
    return Table(
        [[Paragraph(value, styles["notice"])]],
        colWidths=[_CONTENT_WIDTH],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8E8")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#E8C978")),
                ("LINEBEFORE", (0, 0), (0, 0), 3, _AMBER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        ),
    )


def _continuity_table(report: LocalFallbackReport, styles: dict[str, ParagraphStyle]) -> Table:
    coverage = "未取得"
    if report.minimum_coverage is not None and report.maximum_coverage is not None:
        coverage = f"{report.minimum_coverage:.1%}–{report.maximum_coverage:.1%}"
    rows = [
        [Paragraph("扫描轮数", styles["head"]), Paragraph(str(report.scan_count), styles["value"])],
        [
            Paragraph("HEALTHY轮数", styles["head"]),
            Paragraph(str(report.healthy_scan_count), styles["value"]),
        ],
        [Paragraph("覆盖率范围", styles["head"]), Paragraph(coverage, styles["value"])],
        [
            Paragraph("运行会话 / 重启", styles["head"]),
            Paragraph(f"{report.runtime_session_count} / {report.restart_count}", styles["value"]),
        ],
        [
            Paragraph("睡眠 / 唤醒", styles["head"]),
            Paragraph(f"{report.sleep_count} / {report.wake_count}", styles["value"]),
        ],
        [Paragraph("连续性事实", styles["head"]), Paragraph(report.continuity, styles["value"])],
    ]
    return _two_column_table(rows, styles)


def _alerts_table(alerts: Sequence[Any], styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            Paragraph("事件", styles["table_head"]),
            Paragraph("状态", styles["table_head"]),
            Paragraph("候选", styles["table_head"]),
            Paragraph("时间", styles["table_head"]),
        ]
    ]
    for alert in alerts:
        names = "、".join(alert.candidate_names) or "未记录候选"
        rows.append(
            [
                Paragraph(_alert_label(alert.trigger_type), styles["table"]),
                Paragraph(_state_label(alert.state), styles["table"]),
                Paragraph(names, styles["table"]),
                Paragraph(str(alert.displayed_at or "未记录时间"), styles["table"]),
            ]
        )
    if len(rows) == 1:
        rows.append(
            [
                Paragraph("当日提醒", styles["table"]),
                Paragraph("无记录", styles["table"]),
                Paragraph("没有可核验候选", styles["table"]),
                Paragraph("", styles["table"]),
            ]
        )
    return Table(
        rows,
        colWidths=[34 * mm, 27 * mm, 76 * mm, _CONTENT_WIDTH - 137 * mm],
        repeatRows=1,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )


def _top3_table(report: LocalFallbackReport, styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            Paragraph("排名", styles["table_head"]),
            Paragraph("股票", styles["table_head"]),
            Paragraph("最后观察", styles["table_head"]),
            Paragraph("板块 / 等级", styles["table_head"]),
            Paragraph("来源", styles["table_head"]),
        ]
    ]
    for index, candidate in enumerate(report.top3, start=1):
        price = (
            f"¥{candidate.price:.2f}" if candidate.price is not None else "提醒/最后观察价格未记录"
        )
        change = (
            f"{candidate.change_pct:+.2f}%" if candidate.change_pct is not None else "涨跌未记录"
        )
        sector = candidate.sector or "板块未记录"
        rows.append(
            [
                Paragraph(f"{index:02d}", styles["table"]),
                Paragraph(f"{candidate.name}<br/>{candidate.code}", styles["table"]),
                Paragraph(f"{price}<br/>{change}", styles["table"]),
                Paragraph(f"{sector}<br/>{candidate.level}", styles["table"]),
                Paragraph(_source_label(candidate.selection_source), styles["table"]),
            ]
        )
    if not report.top3:
        rows.append(
            [
                Paragraph("—", styles["table"]),
                Paragraph("当日未形成可核验的稳定 Top3", styles["table"]),
                Paragraph("", styles["table"]),
                Paragraph("", styles["table"]),
                Paragraph("", styles["table"]),
            ]
        )
    return Table(
        rows,
        colWidths=[13 * mm, 45 * mm, 43 * mm, 46 * mm, _CONTENT_WIDTH - 147 * mm],
        repeatRows=1,
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _SOFT]),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )


def _two_column_table(rows: list[list[Any]], styles: dict[str, ParagraphStyle]) -> Table:
    return Table(
        rows,
        colWidths=[39 * mm, _CONTENT_WIDTH - 39 * mm],
        style=TableStyle(
            [
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, _SOFT]),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )


def _status_box(
    title: str, value: str, accent: colors.Color, styles: dict[str, ParagraphStyle]
) -> Table:
    return Table(
        [[Paragraph(title, styles["head"]), Paragraph(value, styles["value"])]],
        colWidths=[30 * mm, _CONTENT_WIDTH - 30 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SOFT),
                ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
                ("LINEBEFORE", (0, 0), (0, 0), 3, accent),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        ),
    )


def _styles() -> dict[str, ParagraphStyle]:
    font, font_medium = registered_font_names()
    base = ParagraphStyle("local-base", fontName=font, fontSize=8.7, leading=12, textColor=_INK)
    return {
        "brand": ParagraphStyle(
            "local-brand", parent=base, fontName=font_medium, fontSize=8.5, textColor=_BLUE
        ),
        "date": ParagraphStyle("local-date", parent=base, alignment=2, textColor=_MUTED),
        "title": ParagraphStyle(
            "local-title",
            parent=base,
            fontName=font_medium,
            fontSize=17,
            leading=22,
            spaceBefore=4,
            spaceAfter=4,
        ),
        "badge": ParagraphStyle(
            "local-badge", parent=base, fontSize=7.5, leading=10, textColor=_MUTED
        ),
        "notice": ParagraphStyle(
            "local-notice",
            parent=base,
            fontName=font_medium,
            fontSize=9.2,
            leading=14,
            textColor=_AMBER,
        ),
        "section": ParagraphStyle(
            "local-section",
            parent=base,
            fontName=font_medium,
            fontSize=11.5,
            leading=15,
            spaceBefore=2,
        ),
        "head": ParagraphStyle(
            "local-head", parent=base, fontName=font_medium, fontSize=8.1, leading=11
        ),
        "value": ParagraphStyle("local-value", parent=base, fontSize=8.2, leading=11),
        "table_head": ParagraphStyle(
            "local-table-head",
            parent=base,
            fontName=font_medium,
            fontSize=7.7,
            leading=10,
            textColor=colors.white,
        ),
        "table": ParagraphStyle("local-table", parent=base, fontSize=7.6, leading=10),
        "body": ParagraphStyle(
            "local-body", parent=base, fontSize=8.4, leading=12, textColor=_MUTED
        ),
    }


def _decorate_page(canvas: Any, document: Any) -> None:
    font, _ = registered_font_names()
    canvas.saveState()
    canvas.setStrokeColor(_LINE)
    canvas.setLineWidth(0.5)
    canvas.line(16 * mm, 13 * mm, A4[0] - 16 * mm, 13 * mm)
    canvas.setFillColor(_MUTED)
    canvas.setFont(font, 7.5)
    canvas.drawString(16 * mm, 8.5 * mm, "StockWatcher | 本地运行总结")
    canvas.drawRightString(A4[0] - 16 * mm, 8.5 * mm, f"{canvas.getPageNumber()}")
    canvas.restoreState()


def _alert_label(value: str) -> str:
    return {
        "scheduled-09:45": "09:45固定提醒",
        "scheduled-14:45": "14:45固定提醒",
        "intraday": "盘中强异动",
    }.get(value, value)


def _state_label(value: str) -> str:
    return {
        "succeeded": "成功",
        "failed": "失败",
        "planned": "待执行",
        "running": "执行中",
        "not_recorded": "未记录",
    }.get(value, value)


def _source_label(value: str) -> str:
    return {
        "scheduled_14_45": "14:45稳定批次",
        "latest_healthy_scan": "15:00前健康轮",
        "latest_alert": "最后提醒",
    }.get(value, value)
