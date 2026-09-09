"""The PySide6 window.

A renderer over :mod:`client.ui.timeline` and :mod:`client.ui.updates`. Every
decision about what a row contains, how rows are ordered, and when the view is
refreshed lives in those modules, in plain Python, so it is testable without a
display. This file turns those answers into widgets.

`requirements.md` Section 8.3 lists what must be on screen: a device selector,
the server endpoint, connect and disconnect, start and stop, the current capture
and server status, and a timeline ordered by source audio time showing speaker,
language, timing, transcript, translation, partial or final state, overlap and
low-confidence markers.

Two rules from Section 8.3 and ADR-0016 D41 that this file must not break:

- Rows are **upserted by** ``segment_id``. A partial arriving fifty times a
  second replaces one row fifty times; it never appends.
- The view is refreshed on a timer from a set of changed identifiers, so one
  changed row does not repaint the other five hundred.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from client.lifecycle import CaptureState
from client.ui.timeline import TimelineRow, TimelineState
from client.ui.updates import ChangeAccumulator, CoalescingScheduler
from protocol.projection import SegmentProjection

COLUMNS = ("Time", "Speaker", "Lang", "Transcript", "Translation", "State")

#: A partial is drawn dimmer than a final. Section 8.3 requires the two to be
#: visually distinguishable, and dimming says "not settled yet" without needing
#: a legend.
PARTIAL_FOREGROUND = QColor(120, 120, 120)
LOW_CONFIDENCE_FOREGROUND = QColor(180, 120, 0)
REJECTED_FOREGROUND = QColor(160, 60, 60)

TRANSLATION_PLACEHOLDER = "translating…"


class TimelineModel(QAbstractTableModel):
    """Adapts :class:`TimelineState` to a Qt view.

    Holds no projection logic of its own. It asks the state for ordered rows and
    reports what changed; deciding what a row *is* happens elsewhere.
    """

    def __init__(self, state: TimelineState) -> None:
        super().__init__()
        self._state = state
        self._rows: list[TimelineRow] = []

    # -- Qt model interface --------------------------------------------------

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return COLUMNS[section]

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(row, index.column())
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground(row)
        if role == Qt.ItemDataRole.FontRole and not row.is_final:
            font = QFont()
            font.setItalic(True)
            return font
        if role == Qt.ItemDataRole.ToolTipRole:
            return "; ".join(row.warnings) or None
        return None

    @staticmethod
    def _display(row: TimelineRow, column: int) -> str:
        match column:
            case 0:
                return row.start_display
            case 1:
                return row.speaker
            case 2:
                return row.language
            case 3:
                return row.transcript
            case 4:
                # Section 8.3: a pending state between final ASR and Qwen
                # returning, so an empty cell never reads as "nothing to say".
                if row.shows_translation_placeholder:
                    return TRANSLATION_PLACEHOLDER
                return row.translation
            case 5:
                return ", ".join(row.warnings) or ("final" if row.is_final else "partial")
            case _:
                return ""

    @staticmethod
    def _foreground(row: TimelineRow) -> QColor | None:
        if row.is_rejected:
            return REJECTED_FOREGROUND
        if row.is_low_confidence:
            return LOW_CONFIDENCE_FOREGROUND
        if not row.is_final:
            return PARTIAL_FOREGROUND
        return None

    # -- refreshing ----------------------------------------------------------

    def refresh(self, changed_segment_ids: set[str]) -> None:
        """Re-read the state, emitting the narrowest signal that covers the change.

        A row whose text changed emits ``dataChanged`` for that row alone. A row
        appearing changes the ordering, which needs a model reset - but that is
        the uncommon case, and conflating the two would repaint the whole
        timeline on every partial.
        """
        previous_ids = [row.segment_id for row in self._rows]
        ordered = self._state.ordered()
        current_ids = [row.segment_id for row in ordered]

        if previous_ids != current_ids:
            self.beginResetModel()
            self._rows = ordered
            self.endResetModel()
            return

        self._rows = ordered
        for index, row in enumerate(ordered):
            if row.segment_id in changed_segment_ids:
                self.dataChanged.emit(
                    self.index(index, 0),
                    self.index(index, len(COLUMNS) - 1),
                )


class MainWindow(QMainWindow):
    """The application window.

    Callbacks are injected rather than wired to a client object, so the window
    can be constructed and driven in a test without a network stack, a device or
    a server.
    """

    def __init__(
        self,
        *,
        timeline: TimelineState | None = None,
        accumulator: ChangeAccumulator | None = None,
        scheduler: CoalescingScheduler | None = None,
        log_directory: Path | None = None,
        on_connect: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        on_start: Callable[[str], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()

        self.timeline = timeline or TimelineState()
        self.accumulator = accumulator or ChangeAccumulator()
        self.scheduler = scheduler or CoalescingScheduler()
        self.log_directory = log_directory

        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_start = on_start
        self._on_stop = on_stop

        self.setWindowTitle("Meeting Translator")
        self.model = TimelineModel(self.timeline)

        self._build()
        self.set_state(CaptureState.IDLE)

        self._timer = QTimer(self)
        self._timer.setInterval(int(self.scheduler.interval_s * 1000))
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Capture device"))
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(320)
        controls.addWidget(self.device_box)

        controls.addWidget(QLabel("Server"))
        self.endpoint_edit = QLineEdit("ws://127.0.0.1:8760")
        controls.addWidget(self.endpoint_edit)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._connect_clicked)
        controls.addWidget(self.connect_button)

        self.start_button = QPushButton("Start meeting")
        self.start_button.clicked.connect(self._start_clicked)
        self.start_button.setEnabled(False)
        controls.addWidget(self.start_button)

        layout.addLayout(controls)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self.state_label = QLabel()
        self.statusBar().addWidget(self.state_label)
        self.log_label = QLabel()
        self.statusBar().addPermanentWidget(self.log_label)
        self._show_log_directory()

    def _show_log_directory(self) -> None:
        """ADR-0016 D42: the resolved path is on screen.

        A person asked to send a meeting log has to be able to find it without
        being talked through a path.
        """
        if self.log_directory is None:
            self.log_label.setText("logs: not started")
        else:
            self.log_label.setText(f"logs: {self.log_directory}")

    # -- state ---------------------------------------------------------------

    def set_state(self, state: CaptureState, detail: str = "") -> None:
        """Show the capture lifecycle state (Section 8.2, AUD-120, UI-050)."""
        self.state = state
        text = f"State: {state}"
        if detail:
            text += f" — {detail}"
        self.state_label.setText(text)

        self.connect_button.setText("Disconnect" if state is not CaptureState.IDLE else "Connect")
        self.start_button.setEnabled(state in (CaptureState.READY, CaptureState.CAPTURING))
        self.start_button.setText(
            "Stop meeting" if state is CaptureState.CAPTURING else "Start meeting"
        )

    def set_devices(self, names: list[str], *, selected: str | None = None) -> None:
        """Populate the device selector (AUD-010, AUD-020)."""
        self.device_box.clear()
        self.device_box.addItems(names)
        if selected and selected in names:
            self.device_box.setCurrentText(selected)

    def show_warning(self, message: str) -> None:
        """Surface a degraded mode or a fallback (UI-150, ADR-0014 D38).

        Section 25.12 requires degradation to be visible, and D38 requires a
        device fallback to be stated rather than applied silently.
        """
        self.statusBar().showMessage(message, 15_000)

    def set_log_directory(self, directory: Path) -> None:
        self.log_directory = directory
        self._show_log_directory()

    # -- projections in, rows out --------------------------------------------

    def apply_projection(self, segment: SegmentProjection) -> None:
        """Take one projection from the reducer and mark its row dirty.

        The window never folds an event. It receives what the shared reducer
        produced (ADR-0008 D13, ADR-0016 D41), so the timeline on screen and the
        history on disk cannot diverge.
        """
        if self.timeline.upsert(segment):
            self.accumulator.record(segment.segment_id)

    def _drain(self) -> None:
        if not self.scheduler.due(has_pending=self.accumulator.pending_count > 0):
            return
        changed = self.accumulator.drain()
        self.scheduler.mark_drained()
        if changed:
            self.model.refresh(changed)

    # -- controls ------------------------------------------------------------

    def _connect_clicked(self) -> None:
        if self.state is CaptureState.IDLE:
            if self._on_connect is not None:
                self._on_connect(self.endpoint_edit.text())
        elif self._on_disconnect is not None:
            self._on_disconnect()

    def _start_clicked(self) -> None:
        if self.state is CaptureState.CAPTURING:
            if self._on_stop is not None:
                self._on_stop()
        elif self._on_start is not None:
            self._on_start(self.device_box.currentText())
