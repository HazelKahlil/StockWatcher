from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QSettings, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QMouseEvent, QScreen
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
        price = QLabel(f"¥{row.price:.2f}")
        price.setObjectName("popupCode")
        layout.addWidget(price)
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
        title: str,
        subtitle: str,
        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
        *,
        open_list_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("alertPopup")
        self.setMinimumWidth(320)
        self._title = title
        self._open_list_callback = open_list_callback
        self._settings = QSettings("StockWatcher", "StockWatcher")
        self._build(rows, title, subtitle, details_callback)

    def _build(
        self,
        rows: tuple[CandidateRow, ...],
        title_text: str,
        subtitle: str,
        details_callback: Callable[[str], None],
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel(title_text)
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
        hint = QLabel("只读观察提醒")
        hint.setObjectName("popupHint")
        bottom.addWidget(hint)
        bottom.addStretch()
        open_list = QPushButton("打开列表")
        open_list.setObjectName("primaryButton")
        open_list.clicked.connect(self._open_list)
        bottom.addWidget(open_list)
        root.addLayout(bottom)

    def _open_list(self) -> None:
        if self._open_list_callback is not None:
            self._open_list_callback()
        self.close()

    @staticmethod
    def _clamp_point(
        area: QRect,
        desired: QPoint,
        *,
        width: int,
        height: int,
    ) -> QPoint:
        maximum_x = max(area.left(), area.right() - width + 1)
        maximum_y = max(area.top(), area.bottom() - height + 1)
        return QPoint(
            min(max(desired.x(), area.left()), maximum_x),
            min(max(desired.y(), area.top()), maximum_y),
        )

    def show_at_bottom_right(self, *, preferred_screen: QScreen | None = None) -> None:
        screens = QGuiApplication.screens()
        stored = self._settings.value("alert/position")
        stored_screen = (
            next(
                (
                    candidate
                    for candidate in screens
                    if isinstance(stored, QPoint)
                    and candidate.availableGeometry().contains(stored)
                ),
                None,
            )
            if isinstance(stored, QPoint)
            else None
        )
        screen = (
            stored_screen
            or preferred_screen
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            self.show()
            return
        area = screen.availableGeometry()
        target_width = max(280, min(460, area.width() - 36))
        self.setFixedWidth(target_width)
        self.adjustSize()
        desired = (
            stored
            if isinstance(stored, QPoint)
            else QPoint(
                area.right() - self.width() - 17,
                area.bottom() - self.height() - 17,
            )
        )
        self.move(
            self._clamp_point(
                area,
                desired,
                width=self.width(),
                height=self.height(),
            )
        )
        self.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("alert/position", self.pos())
        super().closeEvent(event)
