"""The Qt window, driven offscreen.

Category B. A thin layer of tests over a thin layer of code: everything worth
deciding about the timeline lives in `client/ui/timeline.py` and
`client/ui/updates.py`, which import no Qt and are tested without one. What
remains here is that the widgets exist, that the model reports what the state
holds, and that the controls change with the lifecycle.

Runs with the offscreen Qt platform, so it needs no display and works in CI. If
PySide6 is missing - which it is on the pod, where the client domain is not
installed - the whole module skips visibly rather than silently passing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="the client dependency domain is not installed here")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from client.lifecycle import CaptureState
from client.ui.app import TRANSLATION_PLACEHOLDER, MainWindow
from client.ui.timeline import TimelineState
from client.ui.updates import ChangeAccumulator
from protocol.enums import SegmentStatus, TranslationStatus
from protocol.projection import SegmentProjection

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "protocol_conformance_fixture"


@pytest.fixture(scope="module")
def qt_app() -> Iterator[QApplication]:
    """One QApplication for the module. Qt permits exactly one per process."""
    app = QApplication.instance() or QApplication([])
    yield app  # type: ignore[misc]


@pytest.fixture
def window(qt_app: QApplication) -> Iterator[MainWindow]:
    win = MainWindow(log_directory=Path("C:/Users/someone/Documents/MeetingTranslator"))
    yield win
    win.close()


def segment(
    segment_id: str = "seg-000001",
    *,
    start_sample: int = 16_000,
    status: SegmentStatus = SegmentStatus.PARTIAL,
    stable_text: str = "",
    text: str = "",
    translation_status: TranslationStatus = TranslationStatus.NOT_APPLICABLE,
    translation_text: str = "",
) -> SegmentProjection:
    return SegmentProjection(
        segment_id=segment_id,
        utterance_id="utt-000001",
        start_sample=start_sample,
        end_sample=start_sample + 32_000,
        status=status,
        stable_text=stable_text,
        text=text,
        translation_status=translation_status,
        translation_text=translation_text,
    )


class TestWindowShell:
    def test_every_section_8_3_control_exists(self, window: MainWindow) -> None:
        assert window.device_box is not None
        assert window.endpoint_edit is not None
        assert window.connect_button is not None
        assert window.start_button is not None
        assert window.table is not None

    def test_the_log_directory_is_on_screen(self, window: MainWindow) -> None:
        """ADR-0016 D42. Someone asked to send a meeting log has to be able to
        find it without being talked through a path."""
        assert "MeetingTranslator" in window.log_label.text()

    def test_the_endpoint_defaults_to_the_tunnel(self, window: MainWindow) -> None:
        """SEC-010: the gateway binds localhost and is reached through the tunnel."""
        assert window.endpoint_edit.text().startswith("ws://127.0.0.1:")

    def test_devices_populate_the_selector(self, window: MainWindow) -> None:
        names = [
            "スピーカー (VMware Virtual Audio (DevTap)) [Loopback]",
            "スピーカー (Teradici Virtual Audio Driver) [Loopback]",
        ]
        window.set_devices(names, selected=names[1])

        assert window.device_box.count() == 2
        assert window.device_box.currentText() == names[1]

    def test_a_non_ascii_device_name_renders_intact(self, window: MainWindow) -> None:
        """The user machine is a Japanese-locale VM; this is its real device."""
        name = "スピーカー (VMware Virtual Audio (DevTap)) [Loopback]"
        window.set_devices([name])
        assert window.device_box.currentText() == name


class TestLifecycleControls:
    def test_start_is_disabled_until_ready(self, window: MainWindow) -> None:
        window.set_state(CaptureState.IDLE)
        assert not window.start_button.isEnabled()

    def test_start_enables_once_ready(self, window: MainWindow) -> None:
        window.set_state(CaptureState.READY)
        assert window.start_button.isEnabled()

    def test_the_buttons_invert_while_capturing(self, window: MainWindow) -> None:
        window.set_state(CaptureState.CAPTURING)
        assert window.start_button.text() == "Stop meeting"
        assert window.connect_button.text() == "Disconnect"

    def test_the_state_is_visible_with_its_reason(self, window: MainWindow) -> None:
        """AUD-120, UI-050. A state with no explanation is half a status."""
        window.set_state(CaptureState.ERROR, "the capture device was removed")
        assert "error" in window.state_label.text()
        assert "removed" in window.state_label.text()

    def test_a_warning_reaches_the_status_bar(self, window: MainWindow) -> None:
        """UI-150 and ADR-0014 D38: a device fallback is stated, not silent."""
        window.show_warning("Configured capture device was not found. Using default.")
        assert "not found" in window.statusBar().currentMessage()


class TestTimelineRendering:
    def _refresh(self, window: MainWindow) -> None:
        """Drain immediately rather than waiting for the timer.

        Testing the coalescing policy by sleeping would make the suite slow and
        occasionally wrong; the policy itself is tested against an injected clock
        in test_ui_and_replay.py.
        """
        changed = window.accumulator.drain()
        window.model.refresh(changed)

    def test_a_projection_becomes_a_row(self, window: MainWindow) -> None:
        window.apply_projection(segment(stable_text="明日の"))
        self._refresh(window)

        assert window.model.rowCount() == 1
        index = window.model.index(0, 3)
        assert window.model.data(index) == "明日の"

    def test_fifty_partials_produce_one_row(self, window: MainWindow) -> None:
        """UI-140, through the widget rather than the state."""
        for revision in range(50):
            window.apply_projection(segment(stable_text="明日" * (revision + 1)))
        self._refresh(window)

        assert window.model.rowCount() == 1

    def test_rows_appear_in_source_audio_order(self, window: MainWindow) -> None:
        window.apply_projection(segment("seg-000002", start_sample=48_000, text="二番目"))
        window.apply_projection(segment("seg-000001", start_sample=16_000, text="一番目"))
        self._refresh(window)

        assert window.model.data(window.model.index(0, 3)) == "一番目"
        assert window.model.data(window.model.index(1, 3)) == "二番目"

    def test_a_partial_is_visually_distinct_from_a_final(self, window: MainWindow) -> None:
        """Section 8.3 requires the two to be distinguishable."""
        window.apply_projection(segment(stable_text="まだ"))
        self._refresh(window)
        partial_font = window.model.data(window.model.index(0, 3), Qt.ItemDataRole.FontRole)

        window.apply_projection(segment(status=SegmentStatus.ACCEPTED, text="決定"))
        self._refresh(window)
        final_font = window.model.data(window.model.index(0, 3), Qt.ItemDataRole.FontRole)

        # The model's data() returns object, as Qt's does; narrowing here is
        # the assertion, not a formality.
        assert isinstance(partial_font, QFont)
        assert partial_font.italic()
        assert final_font is None

    def test_a_pending_translation_shows_a_placeholder(self, window: MainWindow) -> None:
        window.apply_projection(
            segment(
                status=SegmentStatus.ACCEPTED,
                text="はい",
                translation_status=TranslationStatus.PENDING,
            )
        )
        self._refresh(window)

        assert window.model.data(window.model.index(0, 4)) == TRANSLATION_PLACEHOLDER

    def test_a_translation_replaces_the_placeholder(self, window: MainWindow) -> None:
        window.apply_projection(
            segment(
                status=SegmentStatus.ACCEPTED,
                text="はい",
                translation_status=TranslationStatus.COMPLETED,
                translation_text="Vâng",
            )
        )
        self._refresh(window)

        assert window.model.data(window.model.index(0, 4)) == "Vâng"

    def test_a_low_confidence_row_is_coloured_and_labelled(self, window: MainWindow) -> None:
        window.apply_projection(segment(status=SegmentStatus.LOW_CONFIDENCE, text="たぶん"))
        self._refresh(window)

        assert window.model.data(window.model.index(0, 5)) == "low confidence"
        assert window.model.data(window.model.index(0, 0), Qt.ItemDataRole.ForegroundRole)

    def test_an_identical_projection_marks_nothing_dirty(self, window: MainWindow) -> None:
        """A repaint for a row that did not change is waste that grows with the
        length of the meeting."""
        window.apply_projection(segment(stable_text="同じ"))
        self._refresh(window)

        window.apply_projection(segment(stable_text="同じ"))

        assert window.accumulator.pending_count == 0

    def test_an_out_of_range_index_returns_nothing(self, window: MainWindow) -> None:
        assert window.model.data(window.model.index(99, 0)) is None

    def test_headers_name_every_column(self, window: MainWindow) -> None:
        headers = [
            window.model.headerData(column, Qt.Orientation.Horizontal)
            for column in range(window.model.columnCount())
        ]
        assert headers == ["Time", "Speaker", "Lang", "Transcript", "Translation", "State"]


class TestControlCallbacks:
    def test_connect_passes_the_endpoint(self, qt_app: QApplication) -> None:
        seen: list[str] = []
        window = MainWindow(on_connect=seen.append)
        try:
            window.endpoint_edit.setText("ws://127.0.0.1:9999")
            window.connect_button.click()
            assert seen == ["ws://127.0.0.1:9999"]
        finally:
            window.close()

    def test_start_passes_the_selected_device(self, qt_app: QApplication) -> None:
        seen: list[str] = []
        window = MainWindow(on_start=seen.append)
        try:
            window.set_devices(["スピーカー [Loopback]"])
            window.set_state(CaptureState.READY)
            window.start_button.click()
            assert seen == ["スピーカー [Loopback]"]
        finally:
            window.close()

    def test_stop_is_called_while_capturing(self, qt_app: QApplication) -> None:
        stopped: list[bool] = []
        window = MainWindow(on_stop=lambda: stopped.append(True))
        try:
            window.set_state(CaptureState.CAPTURING)
            window.start_button.click()
            assert stopped == [True]
        finally:
            window.close()


class TestSharedState:
    def test_the_window_uses_the_injected_timeline_and_accumulator(
        self, qt_app: QApplication
    ) -> None:
        """The asyncio side owns these; the window borrows them (ADR-0016 D41)."""
        timeline = TimelineState()
        accumulator = ChangeAccumulator()
        window = MainWindow(timeline=timeline, accumulator=accumulator)
        try:
            window.apply_projection(segment(stable_text="共有"))
            assert timeline.count == 1
            assert accumulator.pending_count == 1
        finally:
            window.close()
