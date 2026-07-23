from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .presenter import CandidateRow, format_change


class AlertPopup(QWidget):
    """One persistent, non-activating, fixed-three-row observation window."""

    row_clicked = Signal(str)

    def __init__(
        self,
        rows: tuple[CandidateRow, ...],
        title: str,
        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._details_callback = details_callback
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("alertPopup")
        self.setFixedWidth(460)
        self.setMinimumHeight(164)
        self._build(rows, title)

    def _build(self, rows: tuple[CandidateRow, ...], title: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(QLabel(title))
        close = QPushButton("关闭")
        close.setObjectName("popupClose")
        close.clicked.connect(self.close)
        heading.addWidget(close)
        root.addLayout(heading)
        for row in rows[:3]:
            panel = QFrame()
            panel.setObjectName("alertRow")
            layout = QHBoxLayout(panel)
            layout.setContentsMargins(10, 7, 10, 7)
            label = QLabel(
                f"{row.name}  {row.code}\n{format_change(row.change_pct)}  ·  {row.level}"
            )
            label.setToolTip("单击复制代码；详情按钮查看可追溯字段")
            layout.addWidget(label, 1)
            copy = QPushButton("复制")
            copy.clicked.connect(lambda _checked=False, code=row.code: self._copy(code))
            layout.addWidget(copy)
            detail = QPushButton("详情")
            detail.clicked.connect(
                lambda _checked=False, code=row.code: self._details_callback(code)
            )
            layout.addWidget(detail)
            root.addWidget(panel)

    def _copy(self, code: str) -> None:
        QGuiApplication.clipboard().setText(code)
        self.row_clicked.emit(code)

    def show_at_bottom_right(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 18, area.bottom() - self.height() - 18)
        self.show()
