from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .presenter import CandidateRow, format_change


class AlertRow(QFrame):
    clicked = Signal(str)

    def __init__(self, rank: int, row: CandidateRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.code = row.code
        self.setObjectName("alertRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        rank_label = QLabel(str(rank))
        rank_label.setObjectName("popupRank")
        rank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rank_label.setFixedSize(30, 30)
        layout.addWidget(rank_label)
        identity = QVBoxLayout()
        identity.setSpacing(1)
        name = QLabel(row.name)
        name.setObjectName("popupName")
        code = QLabel(row.code)
        code.setObjectName("popupCode")
        identity.addWidget(name)
        identity.addWidget(code)
        layout.addLayout(identity, 1)
        change = QLabel(format_change(row.change_pct))
        change.setObjectName("popupChange")
        layout.addWidget(change)
        level = QLabel(row.level)
        level.setObjectName("levelBadge")
        level.setProperty("level", row.level)
        level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level.setFixedWidth(48)
        layout.addWidget(level)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.code)
        super().mousePressEvent(event)


class AlertPopup(QWidget):
    """One persistent, non-activating, fixed-three-row observation window."""

    def __init__(
        self,
        rows: tuple[CandidateRow, ...],
        subtitle: str,
        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("本轮观察提醒")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("alertPopup")
        self.setFixedWidth(460)
        self._build(rows, subtitle, details_callback)

    def _build(
        self,
        rows: tuple[CandidateRow, ...],
        subtitle: str,
        details_callback: Callable[[str], None],
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel("本轮观察提醒")
        title.setObjectName("popupTitle")
        heading.addWidget(title)
        heading.addStretch()
        close = QPushButton("关闭")
        close.setObjectName("popupClose")
        close.clicked.connect(self.close)
        heading.addWidget(close)
        root.addLayout(heading)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("popupSubtitle")
        root.addWidget(subtitle_label)
        for rank, row in enumerate(rows[:3], start=1):
            panel = AlertRow(rank, row)
            panel.clicked.connect(details_callback)
            root.addWidget(panel)

        bottom = QHBoxLayout()
        hint = QLabel("本地测试中")
        hint.setObjectName("popupHint")
        bottom.addWidget(hint)
        bottom.addStretch()
        open_list = QPushButton("打开列表")
        open_list.setObjectName("primaryButton")
        open_list.clicked.connect(self.close)
        bottom.addWidget(open_list)
        root.addLayout(bottom)

    def show_at_bottom_right(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 18, area.bottom() - self.height() - 18)
        self.show()
